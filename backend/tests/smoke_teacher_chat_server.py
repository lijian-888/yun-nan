"""Actual authenticated HTTP + model + saved chat test. Removes only its test session."""
import argparse
import base64
import getpass
import hashlib
import json
import secrets
import time
from urllib.parse import parse_qs, urlparse

import httpx
from bs4 import BeautifulSoup


def login(client, base, username, password):
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    response = client.get(base + "/auth/realms/rice-research/protocol/openid-connect/auth", params={
        "client_id":"rice-research-web", "redirect_uri":base + "/", "response_type":"code",
        "scope":"openid", "state":secrets.token_urlsafe(16), "code_challenge":challenge,"code_challenge_method":"S256"})
    for _ in range(4):
        if response.is_redirect:
            location = response.headers["location"]
            code = parse_qs(urlparse(location).query).get("code")
            if code:
                token = client.post(base + "/auth/realms/rice-research/protocol/openid-connect/token", data={
                    "grant_type":"authorization_code","client_id":"rice-research-web","redirect_uri":base + "/",
                    "code":code[0],"code_verifier":verifier})
                token.raise_for_status()
                return token.json()["access_token"]
            response = client.get(location)
            continue
        form = BeautifulSoup(response.text, "html.parser").find("form", id="kc-form-login")
        if not form:
            raise RuntimeError("账号登录未完成：没有可提交的常规登录表单。")
        action = form["action"]
        if urlparse(action).hostname != urlparse(base).hostname:
            raise RuntimeError("认证表单指向非预期主机，未提交凭据。")
        response = client.post(action, data={"username":username,"password":password,"credentialId":""})
    raise RuntimeError("账号登录未完成，需要检查账号状态。")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base",default="https://172.16.123.193:19443")
    parser.add_argument("--username",default="ynaas.researcher")
    parser.add_argument("--login-only",action="store_true")
    args = parser.parse_args()
    password = getpass.getpass("Researcher password: ")
    # Existing private-intranet self-signed endpoint; this does not change server/browser policy.
    with httpx.Client(verify=False,timeout=240,follow_redirects=False) as client:
        token = login(client,args.base,args.username,password)
        del password
        client.headers["Authorization"] = "Bearer " + token
        context = client.get(args.base + "/api/context")
        context.raise_for_status()
        print("Authenticated HTTP: PASS",flush=True)
        if args.login_only:
            return
        created = client.post(args.base + "/api/research/sessions",json={"title":"原始表格查询部署自测（临时）"})
        created.raise_for_status()
        session_id = created.json()["id"]
        def ask(question, **extra):
            start = time.monotonic()
            with client.stream("POST",args.base + f"/api/research/sessions/{session_id}/chat/stream",json={
                "content":question,"knowledge_scope":"public","idempotency_key":secrets.token_hex(16),**extra}) as response:
                response.raise_for_status()
                event = ""
                for line in response.iter_lines():
                    if line.startswith("event:"):
                        event = line[6:].strip()
                    elif line.startswith("data:"):
                        data = json.loads(line[5:].strip())
                        if event == "status":
                            print(data.get("label",""),flush=True)
                        elif event == "error":
                            raise RuntimeError(json.dumps(data,ensure_ascii=False))
                        elif event == "complete":
                            message = data["message"]
                            print(json.dumps({"question":question,"answer":message["content"],"seconds":round(time.monotonic()-start,2)},ensure_ascii=False),flush=True)
                            return message
            raise RuntimeError("SSE did not return complete")
        def scope(message):
            return next(s for s in message["operation_state"] if s["state"] == "teacher_file_fact_scope")
        try:
            first = ask("黄平老师原始文件里，HD218在2024年的株高是多少？")
            assert scope(first)["records"][0]["value"] == "83"
            assert first["evidence"][-1]["source_location"]["cell"] == "H4"
            ambiguous = ask("老师原始文件中，DT1在2024年的株高是多少？")
            assert scope(ambiguous)["status"] == "ambiguous" and not scope(ambiguous)["records"]
            marker = next(s for s in ambiguous["operation_state"] if s["state"] == "research_clarification")
            selected = ask(marker["options"][0]["answer"],clarification_message_id=ambiguous["id"],clarification_action="answer")
            assert scope(selected)["status"] == "found" and len(scope(selected)["records"]) == 1
            direct = ask("查黄平老师原始文件2023勐海考种数据(1).xlsx，DT1在2023年的株高。")
            assert scope(direct)["records"][0]["value"] == "96"
            changed = ask("换成DT2，其他条件不变。")
            assert scope(changed)["records"][0]["value"] == "98"
            assert any(i["value"] == "DT2" for i in scope(changed)["records"][0]["identities"])
            amylose = ask("还是DT1，查该文件2022年的直链淀粉含量。")
            assert scope(amylose)["records"][0]["value"] == "0.1408"
            assert scope(amylose)["records"][0]["cell"] == "Q3"
            missing = ask("老师原始文件中，不存在材料YNAAS999999在2024年的株高是多少？")
            assert scope(missing)["status"] == "not_found" and not scope(missing)["records"]
            print("Actual model + HTTP + clarification + switched material + metric year + missing data: PASS",flush=True)
        finally:
            removed = client.delete(args.base + f"/api/research/sessions/{session_id}")
            print(f"Temporary test session cleanup: {removed.status_code}",flush=True)


if __name__ == "__main__":
    main()
