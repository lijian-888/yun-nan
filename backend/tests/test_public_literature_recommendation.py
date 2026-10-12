import json
import unittest
from unittest.mock import patch

import httpx

from app.ai_gateway import AIProviderSettings
from app.dialogue_orchestration import plan_dialogue
from app.research_agent import _requested_evidence_unavailable_answer
from app.research_search import build_public_web_context, search_public_references


class PublicLiteratureRecommendationTests(unittest.IsolatedAsyncioTestCase):
    async def test_recommendation_without_rank_metrics_keeps_candidates_and_suitability_order(self):
        question = "检索一篇目前最火的水稻育种相关的文献。"
        provider = AIProviderSettings("cherryin", "https://model.example/v1", "test", "test", True)
        recent = "https://journal.example.org/article/doi/10.1234/recent"
        older = "https://www.example.edu.cn/papers/older.html"
        seen = []

        def handler(request):
            payload = json.loads(request.content)
            seen.append((request.url.path, payload))
            if request.url.path == "/v1/chat/completions":
                if "relevant_ids" in payload["messages"][0]["content"]:
                    response = {"relevant_ids": [1, 0]}
                else:
                    response = {"intent": "general", "source": "unspecified", "include_counts": False,
                        "needs_web": True, "web_question": "检索水稻育种文献，推荐值得关注的研究；无热度数据时说明限制。",
                        "web_query": "rice breeding recent advances"}
                return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})
            if request.url.path == "/search":
                return httpx.Response(200, json={"results": [
                    {"title": "水稻育种综述", "url": older, "content": "2009年水稻育种方法综述。"},
                    {"title": "Recent advances in rice breeding", "url": recent,
                     "content": "2026年研究，讨论水稻育种方法进展。未提供引用量或热度排名。"},
                    {"title": "AnythingLLM知识库搭建", "url": "https://www.example.edu.cn/ai/tutorial",
                     "content": "大模型知识库软件安装教程。"},
                ]})
            self.assertEqual(request.url.path, "/extract")
            self.assertEqual(payload["urls"], [recent, older])
            return httpx.Response(200, json={"results": [
                {"url": recent, "raw_content": "Recent advances in rice breeding，2026年研究论文。"},
                {"url": older, "raw_content": "水稻育种综述，2009年发表于高校学报。"},
            ]})

        real_client = httpx.AsyncClient
        with patch.dict("os.environ", {"TAVILY_API_KEY": "test"}), patch("httpx.AsyncClient",
                side_effect=lambda **kwargs: real_client(transport=httpx.MockTransport(handler), **kwargs)):
            plan = await plan_dialogue(provider=provider, question=question, history=[])
            sources, note = await search_public_references(question, model_requested=plan.needs_web,
                research_question=plan.web_question, search_query=plan.web_query, provider=provider)
        self.assertEqual([item.url for item in sources], [recent, older])
        self.assertIsNone(note)
        search_request = next(payload for path, payload in seen if path == "/search")
        self.assertEqual(search_request["query"], plan.web_query)
        self.assertEqual(search_request["include_domains"], [])
        context = build_public_web_context(sources, note, research_question=plan.web_question)
        self.assertIsNone(_requested_evidence_unavailable_answer(question,
            "本次问题未检索到可用于支撑结论的本地知识库资料。", context))


if __name__ == "__main__":
    unittest.main()
