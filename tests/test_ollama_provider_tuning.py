import json
import unittest
from unittest.mock import patch

from ai_job_search.model_provider import ModelRequest, ModelProviderError
from ai_job_search.providers.ollama import OllamaProvider


class OllamaProviderTuningTests(unittest.TestCase):
    @patch("ai_job_search.providers.ollama.urllib.request.urlopen")
    def test_build_payload_uses_num_ctx_and_keep_alive_defaults(self, _urlopen) -> None:
        provider = OllamaProvider(model="qwen2.5:14b")
        payload = provider._build_payload(  # noqa: SLF001
            ModelRequest(system_prompt="s", user_prompt="u", response_format="text")
        )

        self.assertEqual(payload["keep_alive"], "0")
        self.assertEqual(payload["options"]["num_ctx"], 2048)

    def test_cuda_oom_http_500_is_classified(self) -> None:
        provider = OllamaProvider(model="qwen2.5:14b")
        self.assertTrue(provider._is_cuda_oom_message("cudaMalloc failed"))  # noqa: SLF001
        self.assertTrue(provider._is_cuda_oom_message("out of memory"))  # noqa: SLF001
        self.assertTrue(provider._is_cuda_oom_message("unable to allocate CUDA"))  # noqa: SLF001
        self.assertTrue(provider._is_cuda_oom_message("failed to allocate CUDA"))  # noqa: SLF001

    def test_fallback_model_attempted_once_on_cuda_oom(self) -> None:
        provider = OllamaProvider(model="qwen2.5:14b", fallback_model="qwen2.5:7b")
        request = ModelRequest(system_prompt="sys", user_prompt="user")

        calls = []

        def fake_post(payload: dict):
            calls.append(payload["model"])
            if len(calls) == 1:
                raise ModelProviderError(
                    "Ollama chat request failed: HTTP 500 CUDA out-of-memory. Model='qwen2.5:14b'."
                )
            return {"message": {"content": "ok"}}

        with patch.object(provider, "_post_chat", side_effect=fake_post):
            response = provider.complete(request)

        self.assertEqual(response.text, "ok")
        self.assertEqual(calls, ["qwen2.5:14b", "qwen2.5:7b"])


if __name__ == "__main__":
    unittest.main()
