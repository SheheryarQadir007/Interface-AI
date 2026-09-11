"""
LLM Client Abstraction

Supports multiple LLM providers (OpenAI, Anthropic, Mock) with
a unified interface for the agent loop.
"""

import os
import json
import base64
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, AsyncIterator
from dataclasses import dataclass
from enum import Enum

from src.config import settings


class LLMProvider(str, Enum):
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    MOCK = "mock"


@dataclass
class LLMMessage:
    role: str  # system, user, assistant
    content: str
    images: List[str] = None  # base64 encoded images


@dataclass
class LLMResponse:
    content: str
    tool_calls: List[Dict[str, Any]] = None
    usage: Dict[str, int] = None
    raw_response: Any = None


class LLMClient(ABC):
    """Abstract base class for LLM clients."""
    
    @abstractmethod
    async def complete(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.1,
        max_tokens: int = 4000,
    ) -> LLMResponse:
        """Get completion from LLM."""
        pass
    
    @abstractmethod
    async def stream_complete(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]] = None,
        **kwargs
    ) -> AsyncIterator[str]:
        """Stream completion from LLM."""
        pass


class OpenAIClient(LLMClient):
    """OpenAI API client."""
    
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or settings.openai_api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or settings.openai_model
        
        if not self.api_key:
            raise ValueError("OpenAI API key required")
        
        try:
            from openai import AsyncOpenAI
            self.client = AsyncOpenAI(api_key=self.api_key)
        except ImportError:
            raise ImportError("openai package required. Install with: pip install openai")
    
    async def complete(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.1,
        max_tokens: int = 4000,
    ) -> LLMResponse:
        # Convert messages
        openai_messages = []
        for msg in messages:
            if msg.images:
                content = [{"type": "text", "text": msg.content}]
                for img in msg.images:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{img}"}
                    })
                openai_messages.append({"role": msg.role, "content": content})
            else:
                openai_messages.append({"role": msg.role, "content": msg.content})
        
        kwargs = {
            "model": self.model,
            "messages": openai_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice
        
        response = await self.client.chat.completions.create(**kwargs)
        
        message = response.choices[0].message
        tool_calls = None
        if message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "arguments": json.loads(tc.function.arguments),
                }
                for tc in message.tool_calls
            ]
        
        return LLMResponse(
            content=message.content or "",
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": response.usage.prompt_tokens,
                "completion_tokens": response.usage.completion_tokens,
                "total_tokens": response.usage.total_tokens,
            },
            raw_response=response,
        )
    
    async def stream_complete(self, messages: List[LLMMessage], **kwargs) -> AsyncIterator[str]:
        openai_messages = []
        for msg in messages:
            openai_messages.append({"role": msg.role, "content": msg.content})
        
        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=openai_messages,
            stream=True,
            **kwargs
        )
        
        async for chunk in stream:
            if chunk.choices[0].delta.content:
                yield chunk.choices[0].delta.content


class AnthropicClient(LLMClient):
    """Anthropic API client."""
    
    def __init__(self, api_key: str = None, model: str = None):
        self.api_key = api_key or settings.anthropic_api_key or os.getenv("ANTHROPIC_API_KEY")
        self.model = model or settings.anthropic_model
        
        if not self.api_key:
            raise ValueError("Anthropic API key required")
        
        try:
            from anthropic import AsyncAnthropic
            self.client = AsyncAnthropic(api_key=self.api_key)
        except ImportError:
            raise ImportError("anthropic package required. Install with: pip install anthropic")
    
    async def complete(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.1,
        max_tokens: int = 4000,
    ) -> LLMResponse:
        # Convert messages - Anthropic uses system prompt separately
        system_prompt = ""
        anthropic_messages = []
        
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            else:
                content = msg.content
                if msg.images:
                    content = [{"type": "text", "text": msg.content}]
                    for img in msg.images:
                        content.append({
                            "type": "image",
                            "source": {"type": "base64", "media_type": "image/png", "data": img}
                        })
                anthropic_messages.append({"role": msg.role, "content": content})
        
        kwargs = {
            "model": self.model,
            "messages": anthropic_messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if system_prompt:
            kwargs["system"] = system_prompt
        
        if tools:
            # Convert OpenAI-style tools to Anthropic format
            kwargs["tools"] = [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "input_schema": t["function"]["parameters"],
                }
                for t in tools
            ]
        
        response = await self.client.messages.create(**kwargs)
        
        content = ""
        tool_calls = None
        
        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                if tool_calls is None:
                    tool_calls = []
                tool_calls.append({
                    "id": block.id,
                    "name": block.name,
                    "arguments": block.input,
                })
        
        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            usage={
                "prompt_tokens": response.usage.input_tokens,
                "completion_tokens": response.usage.output_tokens,
                "total_tokens": response.usage.input_tokens + response.usage.output_tokens,
            },
            raw_response=response,
        )
    
    async def stream_complete(self, messages: List[LLMMessage], **kwargs) -> AsyncIterator[str]:
        # Similar to complete but streaming
        async for text in self._stream_impl(messages, **kwargs):
            yield text
    
    async def _stream_impl(self, messages: List[LLMMessage], **kwargs) -> AsyncIterator[str]:
        system_prompt = ""
        anthropic_messages = []
        
        for msg in messages:
            if msg.role == "system":
                system_prompt = msg.content
            else:
                anthropic_messages.append({"role": msg.role, "content": msg.content})
        
        kwargs["model"] = self.model
        kwargs["messages"] = anthropic_messages
        kwargs["stream"] = True
        
        if system_prompt:
            kwargs["system"] = system_prompt
        
        async with self.client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                yield text


class MockLLMClient(LLMClient):
    """Mock LLM client for testing without API keys."""
    
    def __init__(self, responses: Dict[str, str] = None):
        self.responses = responses or {}
        self.call_count = 0
    
    async def complete(
        self,
        messages: List[LLMMessage],
        tools: List[Dict[str, Any]] = None,
        tool_choice: str = "auto",
        temperature: float = 0.1,
        max_tokens: int = 4000,
    ) -> LLMResponse:
        self.call_count += 1
        
        # Extract the last user message to determine response
        last_user_msg = ""
        for msg in reversed(messages):
            if msg.role == "user":
                last_user_msg = msg.content
                break
        
        # Generate mock tool call based on the conversation
        tool_call = self._generate_mock_tool_call(last_user_msg, tools, self.call_count)
        
        return LLMResponse(
            content=tool_call.get("reasoning", "") if tool_call else "",
            tool_calls=[tool_call] if tool_call else None,
            usage={"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
        )
    
    def _generate_mock_tool_call(self, user_msg: str, tools: List[Dict], call_count: int) -> Dict:
        """Generate a mock tool call based on the user message and call count."""
        user_lower = user_msg.lower()
        
        # Check for available tools
        available_tools = []
        if tools:
            for tool in tools:
                if tool.get("function"):
                    available_tools.append(tool["function"]["name"])
        
        # State machine for the demo flow
        # call_count 1: navigate to member lookup
        # call_count 2: type member_id
        # call_count 3: click search
        # call_count 4: extract savings balance
        # call_count 5: done
        
        if call_count == 1 and "navigate" in available_tools:
            return {
                "id": f"call_{call_count}",
                "name": "navigate",
                "arguments": {"url": "http://localhost:5002/member-lookup", "wait_until": "networkidle"},
                "reasoning": "Navigate to member lookup page"
            }
        
        if call_count == 2 and "type_text" in available_tools:
            return {
                "id": f"call_{call_count}",
                "name": "type_text",
                "arguments": {"selector": "input[name='member_id']", "text": "12345", "clear_first": True},
                "reasoning": "Enter member ID 12345"
            }
        
        if call_count == 3 and "click" in available_tools:
            return {
                "id": f"call_{call_count}",
                "name": "click",
                "arguments": {"selector": "button[type='submit']", "strategy": "css"},
                "reasoning": "Click Search button"
            }
        
        if call_count == 4 and "extract_text" in available_tools:
            return {
                "id": f"call_{call_count}",
                "name": "extract_text",
                "arguments": {"selector": "td:contains('Savings') + td", "attribute": "text"},
                "reasoning": "Extract savings balance"
            }
        
        if call_count == 5 and "get_page_state" in available_tools:
            return {
                "id": f"call_{call_count}",
                "name": "get_page_state",
                "arguments": {},
                "reasoning": "Verify final state"
            }
        
        # Default: try navigate
        if "navigate" in available_tools:
            return {
                "id": f"call_{call_count}",
                "name": "navigate",
                "arguments": {"url": "http://localhost:5000/member-lookup", "wait_until": "networkidle"},
                "reasoning": "Navigate to member lookup page"
            }
        
        return None
    
    async def stream_complete(self, messages: List[LLMMessage], **kwargs) -> AsyncIterator[str]:
        response = await self.complete(messages, **kwargs)
        yield response.content


def create_llm_client(provider: str = None, **kwargs) -> LLMClient:
    """Factory function to create LLM client."""
    provider = provider or settings.llm_provider
    
    if provider == "openai":
        return OpenAIClient(**kwargs)
    elif provider == "anthropic":
        return AnthropicClient(**kwargs)
    elif provider == "mock":
        return MockLLMClient(**kwargs)
    else:
        raise ValueError(f"Unknown LLM provider: {provider}")