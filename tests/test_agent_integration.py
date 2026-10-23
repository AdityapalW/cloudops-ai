"""Offline ADK boundary tests with a scripted model, not model-quality evaluations."""

import json
import unittest
from unittest.mock import patch

from google.adk.agents import Agent
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from pydantic import PrivateAttr

from cloudops_agent.agent import root_agent


class ScriptedModel(BaseLlm):
    model: str = "offline-scripted-model"
    tool_name: str = "diagnose_gcp_service"
    tool_args: dict = {"service_name": "api", "hours": 1}
    _calls: int = PrivateAttr(default=0)

    async def generate_content_async(self, llm_request, stream=False):
        self._calls += 1
        if self._calls == 1:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(function_call=types.FunctionCall(name=self.tool_name, args=self.tool_args))
                    ],
                )
            )
            return
        responses = [
            part.function_response
            for content in llm_request.contents
            for part in (content.parts or [])
            if part.function_response
        ]
        if not responses:
            raise AssertionError("ADK did not return the tool result to the model")
        payload = responses[-1].response
        yield LlmResponse(content=types.Content(role="model", parts=[types.Part(text=json.dumps(payload))]))


class AgentIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def run_agent(self, model):
        agent = Agent(
            name="cloudops_agent", model=model, instruction=root_agent.instruction, tools=root_agent.tools
        )
        sessions = InMemorySessionService()
        session = await sessions.create_session(app_name="cloudops_test", user_id="tester")
        runner = Runner(app_name="cloudops_test", agent=agent, session_service=sessions)
        events = []
        async for event in runner.run_async(
            user_id="tester",
            session_id=session.id,
            new_message=types.Content(role="user", parts=[types.Part(text="Inspect the service.")]),
        ):
            events.append(event)
        final = next(event for event in reversed(events) if event.is_final_response())
        return json.loads(final.content.parts[0].text), events

    async def test_structured_tool_error_survives_adk_round_trip(self):
        with patch("cloudops_agent.gcp._session", side_effect=AssertionError("unexpected network")):
            payload, events = await self.run_agent(
                ScriptedModel(tool_args={"service_name": "api", "hours": 0})
            )
        result = payload.get("result", payload)
        self.assertEqual(result["error"]["code"], "invalid_argument")
        self.assertTrue(any(event.get_function_calls() for event in events))
        self.assertTrue(any(event.get_function_responses() for event in events))

    async def test_snapshot_tool_is_callable_through_registered_agent(self):
        with patch("cloudops_agent.snapshots._context", return_value=("p", "r")):
            with patch(
                "cloudops_agent.snapshots.list_gcp_services",
                return_value={"services": [], "truncated": False},
            ):
                payload, _ = await self.run_agent(
                    ScriptedModel(tool_name="snapshot_gcp_services", tool_args={"limit": 1})
                )
        result = payload.get("result", payload)
        self.assertTrue(result["complete"])
        self.assertEqual(result["kind"], "cloudops.cloud_run_configuration")
