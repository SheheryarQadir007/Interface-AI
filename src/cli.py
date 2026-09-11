"""
Main CLI Entry Point

Commands for running discovery, replay, and managing artifacts.
"""

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from src.config import settings
from src.artifacts.schema import AutomationArtifact, InputParameter
from src.agent.discovery import DiscoveryAgent, AgentConfig
from src.replay.engine import replay_artifact, ReplayResult
from src.surface.web import WebSurface
from src.evidence.collector import EvidenceCollector, setup_structured_logging
from src.demo_app.app import app as demo_app

app = typer.Typer(name="cua", help="Computer-Use Automation System")
console = Console()


def setup_logging():
    """Setup structured logging."""
    setup_structured_logging(log_level="INFO")


@app.command()
def start_demo_app(
    port: int = typer.Option(5000, help="Port to run demo app on"),
    host: str = typer.Option("0.0.0.0", help="Host to bind to"),
):
    """Start the demo banking application."""
    console.print(f"[green]Starting demo banking app on http://{host}:{port}[/green]")
    console.print("Press Ctrl+C to stop")
    
    # Update settings
    settings.demo_app_port = port
    settings.demo_app_url = f"http://{host}:{port}"
    
    demo_app.run(host=host, port=port, debug=True, use_reloader=False)


@app.command()
def discover(
    goal: str = typer.Argument(..., help="Natural language goal to accomplish"),
    url: str = typer.Option(None, help="Starting URL (defaults to demo app)"),
    inputs: str = typer.Option("{}", help="JSON string of input parameters"),
    output: str = typer.Option("artifacts/discovered_artifact.json", help="Output artifact path"),
    max_steps: int = typer.Option(20, help="Maximum steps"),
    headless: bool = typer.Option(False, help="Run browser headless"),
    llm_provider: str = typer.Option("mock", help="LLM provider: mock, openai, anthropic"),
    evidence_dir: str = typer.Option("evidence", help="Evidence directory"),
):
    """Run LLM-driven discovery to accomplish a goal."""
    
    setup_logging()
    
    # Parse inputs
    try:
        input_params = json.loads(inputs)
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON for inputs: {e}[/red]")
        raise typer.Exit(1)
    
    # Default URL
    if url is None:
        url = settings.demo_app_url
    
    console.print(Panel.fit(
        f"[bold]Discovery Run[/bold]\n"
        f"Goal: {goal}\n"
        f"URL: {url}\n"
        f"Inputs: {input_params}\n"
        f"LLM: {llm_provider}",
        title="Starting Discovery"
    ))
    
    async def run_discovery():
        # Create surface
        surface = WebSurface(config={"headless": headless})
        await surface.initialize()
        
        try:
            # Navigate to starting URL
            await surface.navigate(url)
            
            # Create evidence collector
            run_id = f"discovery_{int(asyncio.get_event_loop().time())}"
            evidence = EvidenceCollector(run_id, evidence_dir)
            
            # Configure agent
            config = AgentConfig(
                max_steps=max_steps,
                llm_provider=llm_provider,
                headless=headless,
            )
            
            # Run discovery
            agent = DiscoveryAgent(surface, config, evidence)
            result = await agent.run(goal, input_params)
            
            # Display result
            if result.status == "success":
                console.print(f"[green]✓ Discovery successful![/green]")
                console.print(f"Artifact ID: {result.artifact_id}")
                console.print(f"Steps: {len(agent.steps_taken)}")
                
                # Save artifact
                artifact = agent._build_artifact()
                os.makedirs(os.path.dirname(output), exist_ok=True)
                artifact.save(output)
                console.print(f"Artifact saved to: {output}")
            else:
                console.print(f"[red]✗ Discovery failed: {result.error}[/red]")
            
            # Show evidence location
            console.print(f"Evidence saved to: {evidence.evidence_dir}")
            
            return result
            
        finally:
            await surface.close()
    
    result = asyncio.run(run_discovery())
    
    if result.status != "success":
        raise typer.Exit(1)


@app.command()
def replay(
    artifact_path: str = typer.Argument(..., help="Path to artifact JSON"),
    inputs: str = typer.Option("{}", help="JSON string of input parameters"),
    url: str = typer.Option(None, help="Starting URL (defaults to demo app)"),
    headless: bool = typer.Option(True, help="Run browser headless"),
    evidence_dir: str = typer.Option("evidence", help="Evidence directory"),
    config_file: str = typer.Option(None, help="Replay config JSON file"),
):
    """Replay an artifact deterministically."""
    
    setup_logging()
    
    # Parse inputs
    try:
        input_params = json.loads(inputs)
    except json.JSONDecodeError as e:
        console.print(f"[red]Invalid JSON for inputs: {e}[/red]")
        raise typer.Exit(1)
    
    # Load config
    replay_config = {}
    if config_file:
        with open(config_file) as f:
            replay_config = json.load(f)
    
    # Default URL
    if url is None:
        url = settings.demo_app_url
    
    console.print(Panel.fit(
        f"[bold]Replay Run[/bold]\n"
        f"Artifact: {artifact_path}\n"
        f"URL: {url}\n"
        f"Inputs: {input_params}",
        title="Starting Replay"
    ))
    
    async def run_replay():
        # Create surface
        surface = WebSurface(config={"headless": headless})
        await surface.initialize()
        
        try:
            # Navigate to starting URL
            await surface.navigate(url)
            
            # Create evidence collector
            run_id = f"replay_{int(asyncio.get_event_loop().time())}"
            evidence = EvidenceCollector(run_id, evidence_dir)
            
            # Run replay
            result = await replay_artifact(
                artifact_path=artifact_path,
                inputs=input_params,
                surface=surface,
                evidence_dir=evidence_dir,
                config=replay_config,
            )
            
            # Display result
            status_colors = {
                "success": "green",
                "business_outcome": "yellow",
                "failure": "red",
                "escalated": "magenta",
            }
            color = status_colors.get(result.status.value, "white")
            
            console.print(f"[{color}]Replay {result.status.value.upper()}[/{color}]")
            console.print(f"Run ID: {result.run_id}")
            console.print(f"Artifact: {result.artifact_id} v{result.artifact_version}")
            console.print(f"Steps executed: {len(result.step_results)}")
            
            if result.business_outcome:
                console.print(f"Business outcome: {result.business_outcome}")
            
            if result.outputs:
                console.print("Outputs:")
                for k, v in result.outputs.items():
                    console.print(f"  {k}: {v}")
            
            if result.error:
                console.print(f"[red]Error: {result.error}[/red]")
                if result.error_details:
                    console.print(f"Details: {json.dumps(result.error_details, indent=2)}")
            
            console.print(f"Evidence saved to: {evidence.evidence_dir}")
            
            return result
            
        finally:
            await surface.close()
    
    result = asyncio.run(run_replay())
    
    if result.status.value == "failure":
        raise typer.Exit(1)


@app.command()
def list_artifacts(
    directory: str = typer.Option("artifacts", help="Artifact directory"),
):
    """List available artifacts."""
    
    artifact_dir = Path(directory)
    if not artifact_dir.exists():
        console.print(f"[yellow]Directory {directory} does not exist[/yellow]")
        return
    
    artifacts = list(artifact_dir.glob("*.json"))
    
    if not artifacts:
        console.print("[yellow]No artifacts found[/yellow]")
        return
    
    table = Table(title="Available Artifacts")
    table.add_column("Name", style="cyan")
    table.add_column("Version", style="green")
    table.add_column("Description", style="white")
    table.add_column("Target App", style="blue")
    table.add_column("Steps", style="magenta")
    
    for artifact_file in artifacts:
        try:
            artifact = AutomationArtifact.load(str(artifact_file))
            table.add_row(
                artifact.metadata.name,
                artifact.metadata.version,
                artifact.metadata.description[:60] + "..." if len(artifact.metadata.description) > 60 else artifact.metadata.description,
                artifact.metadata.target_application,
                str(len(artifact.steps)),
            )
        except Exception as e:
            table.add_row(
                artifact_file.stem,
                "?",
                f"Error loading: {e}",
                "?",
                "?",
            )
    
    console.print(table)


@app.command()
def validate_artifact(
    artifact_path: str = typer.Argument(..., help="Path to artifact JSON"),
):
    """Validate an artifact schema."""
    
    try:
        artifact = AutomationArtifact.load(artifact_path)
        console.print(f"[green]✓ Artifact is valid[/green]")
        console.print(f"Name: {artifact.metadata.name}")
        console.print(f"Version: {artifact.metadata.version}")
        console.print(f"Description: {artifact.metadata.description}")
        console.print(f"Steps: {len(artifact.steps)}")
        console.print(f"Inputs: {len(artifact.inputs)}")
        console.print(f"Outputs: {len(artifact.outputs)}")
        console.print(f"Known outcomes: {len(artifact.known_outcomes)}")
        
        # Show steps
        table = Table(title="Steps")
        table.add_column("Step ID", style="cyan")
        table.add_column("Action", style="green")
        table.add_column("Description", style="white")
        table.add_column("Target", style="yellow")
        
        for step in artifact.steps:
            target_str = ""
            if step.target:
                target_str = f"{step.target.primary.value}:{step.target.primary_value}"
            table.add_row(step.step_id, step.action.value, step.description[:50], target_str)
        
        console.print(table)
        
    except Exception as e:
        console.print(f"[red]✗ Invalid artifact: {e}[/red]")
        raise typer.Exit(1)


@app.command()
def test_escalation(
    escalation_id: str = typer.Option(None, help="Escalation ID to respond to"),
    action: str = typer.Option("resolved", help="Action taken by operator"),
):
    """Simulate operator response to escalation (for testing)."""
    
    if not escalation_id:
        console.print("[red]Escalation ID required[/red]")
        raise typer.Exit(1)
    
    escalation_handler.simulate_operator_response(
        escalation_id=escalation_id,
        action_taken=action,
        operator_id="test_operator",
    )
    console.print(f"[green]Simulated operator response for {escalation_id}[/green]")


@app.command()
def run_full_demo(
    goal: str = typer.Option("look up member 12345 and read their current savings balance", help="Goal for discovery"),
    headless: bool = typer.Option(False, help="Run headless"),
    llm_provider: str = typer.Option("mock", help="LLM provider"),
):
    """Run complete end-to-end demo: discovery + replay."""
    
    setup_logging()
    
    console.print(Panel.fit(
        "[bold]Full End-to-End Demo[/bold]\n"
        "1. Start demo app\n"
        "2. Run discovery\n"
        "3. Save artifact\n"
        "4. Run replay\n"
        "5. Show evidence",
        title="Demo"
    ))
    
    async def run_demo():
        # Start demo app in background
        import subprocess
        import time
        
        console.print("[blue]Starting demo app...[/blue]")
        demo_process = subprocess.Popen([
            sys.executable, "-m", "uvicorn", "src.demo_app.app:app",
            "--host", "0.0.0.0", "--port", "5000"
        ])
        
        # Wait for app to start
        await asyncio.sleep(3)
        
        try:
            # Discovery
            console.print("\n[bold blue]=== DISCOVERY PHASE ===[/bold blue]")
            
            surface = WebSurface(config={"headless": headless})
            await surface.initialize()
            await surface.navigate(settings.demo_app_url)
            
            run_id = f"demo_discovery_{int(asyncio.get_event_loop().time())}"
            evidence = EvidenceCollector(run_id, "evidence")
            
            config = AgentConfig(
                max_steps=15,
                llm_provider=llm_provider,
                headless=headless,
            )
            
            agent = DiscoveryAgent(surface, config, evidence)
            result = await agent.run(goal, {"member_id": "12345"})
            
            if result.status != "success":
                console.print(f"[red]Discovery failed: {result.error}[/red]")
                return
            
            artifact = agent._build_artifact()
            artifact_path = "artifacts/demo_artifact.json"
            os.makedirs("artifacts", exist_ok=True)
            artifact.save(artifact_path)
            console.print(f"[green]Artifact saved to {artifact_path}[/green]")
            
            await surface.close()
            
            # Replay
            console.print("\n[bold blue]=== REPLAY PHASE ===[/bold blue]")
            
            surface2 = WebSurface(config={"headless": headless})
            await surface2.initialize()
            await surface2.navigate(settings.demo_app_url)
            
            run_id2 = f"demo_replay_{int(asyncio.get_event_loop().time())}"
            evidence2 = EvidenceCollector(run_id2, "evidence")
            
            replay_result = await replay_artifact(
                artifact_path=artifact_path,
                inputs={"member_id": "12345"},
                surface=surface2,
                evidence_dir="evidence",
            )
            
            console.print(f"\n[bold]Replay Result: {replay_result.status.value.upper()}[/bold]")
            if replay_result.outputs:
                for k, v in replay_result.outputs.items():
                    console.print(f"  {k}: {v}")
            
            await surface2.close()
            
            # Show evidence
            console.print("\n[bold blue]=== EVIDENCE ===[/bold blue]")
            console.print(f"Discovery evidence: {evidence.evidence_dir}")
            console.print(f"Replay evidence: {evidence2.evidence_dir}")
            
        finally:
            demo_process.terminate()
    
    asyncio.run(run_demo())


if __name__ == "__main__":
    app()