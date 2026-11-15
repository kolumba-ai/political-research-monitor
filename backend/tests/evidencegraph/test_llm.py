import json

import httpx

from evidencegraph.llm import OpenAILLM


def test_openai_llm_structured_output_and_usage():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {"content": '{"a": 1}'}}],
                                         "usage": {"prompt_tokens": 12, "completion_tokens": 3}})

    llm = OpenAILLM("k", "gpt-4.1-mini", transport=httpx.MockTransport(handler), sleep=lambda s: None)
    out, usage = llm.extract("sys", "prompt", {"name": "x", "input_schema": {"type": "object"}})
    assert out == {"a": 1} and usage == {"input_tokens": 12, "output_tokens": 3}
    assert seen[0]["response_format"]["json_schema"]["strict"] is True
    assert seen[0]["messages"][0] == {"role": "system", "content": "sys"}
