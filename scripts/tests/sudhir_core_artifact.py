"""Focused artifact checks. Uses an already-built core; never invokes Cargo."""
import argparse, json, os, queue, subprocess, sys, tempfile, threading, time
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class RPC:
    def __init__(self, binary, home, extra_args=None, extra_env=None):
        self.messages = queue.Queue()
        self.events = []
        self.next_id = 0
        self.log = tempfile.TemporaryFile(mode="w+")
        self.os_home = tempfile.TemporaryDirectory(prefix="sudhir-artifact-os-home-")
        env = {**os.environ, "HOME": self.os_home.name, "CODEX_HOME": str(home), "SUDHIR_CODEX_GATEWAY_TOKEN": "fixture-secret"}
        env.update(extra_env or {})
        for key in ["OPENAI_API_KEY", "CODEX_API_KEY"]: env.pop(key, None)
        self.p = subprocess.Popen([str(binary), *(extra_args or []), "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log, text=True, env=env)
        def receive():
            for line in self.p.stdout:
                try: self.messages.put(json.loads(line))
                except ValueError: pass
        threading.Thread(target=receive, daemon=True).start()
        try:
            self.identity = self.request("initialize", {"clientInfo": {"name": "sudhir_artifact_probe", "version": "1.0.0"}, "capabilities": {"experimentalApi": True}})
        except Exception:
            self.close()
            print(self.diagnostics, file=sys.stderr)
            raise
        self.send({"method": "initialized"})
    def send(self, message):
        self.p.stdin.write(json.dumps(message)+"\n"); self.p.stdin.flush()
    def request(self, method, params):
        self.next_id += 1
        self.send({"id":self.next_id,"method":method,"params":params})
        deadline=time.monotonic()+45
        while time.monotonic()<deadline:
            message=self.messages.get(timeout=max(0.01,deadline-time.monotonic()))
            if message.get("id")==self.next_id:
                if "error" in message: raise AssertionError((method,message["error"]))
                return message["result"]
            self.events.append(message)
        raise TimeoutError(method)
    def turn(self, thread_id, model, text, *, expected_status="completed"):
        result=self.request("turn/start",{"threadId":thread_id,"model":model,"input":[{"type":"text","text":text}]})
        turn_id=result["turn"]["id"]
        deadline=time.monotonic()+60
        while time.monotonic()<deadline:
            for event in self.events:
                if event.get("method")=="turn/completed" and event.get("params",{}).get("turn",{}).get("id")==turn_id:
                    assert event["params"]["turn"]["status"]==expected_status,event
                    return
            self.events.append(self.messages.get(timeout=max(0.01,deadline-time.monotonic())))
        raise TimeoutError("turn")
    def close(self):
        if self.p.stdin.closed: return
        self.p.stdin.close()
        try: self.p.wait(timeout=10)
        except subprocess.TimeoutExpired: self.p.terminate();self.p.wait(timeout=10)
        self.log.seek(0); self.diagnostics=self.log.read();self.log.close();self.os_home.cleanup()

def verify(binary, repo, baseline=False, legacy_binary=None):
    captured=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            body=json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            captured.append({"path":self.path,"body":body})
            inputs=body.get("input",[])
            compact="SUDHIR_COMPACT" in json.dumps(inputs)
            tool="SUDHIR_TOOL_PROBE" in json.dumps(inputs)
            outputs=[x for x in inputs if x.get("type")=="function_call_output"]
            rid="r"+str(len(captured)); tokens=100 if compact or tool else 5000
            if tool and not outputs:
                cmd='if [ -z "${SUDHIR_CODEX_GATEWAY_TOKEN+x}" ]; then printf token_hidden; else printf token_leak; fi; p=$(command -v apply_patch); if [ -f "$p" ] && [ ! -L "$p" ]; then printf alias_regular; fi'
                item={"type":"function_call","id":"fc1","call_id":"call1","name":"exec_command","arguments":json.dumps({"cmd":cmd,"max_output_tokens":100})}
            else:
                item={"type":"message","role":"assistant","id":"m"+rid,"content":[{"type":"output_text","text":"SUDHIR_SUMMARY" if compact else "SUDHIR_OK"}]}
            events=[{"type":"response.created","response":{"id":rid}}, {"type":"response.output_item.done","item":item}, {"type":"response.completed","response":{"id":rid,"usage":{"input_tokens":tokens,"output_tokens":0,"total_tokens":tokens}}}]
            encoded="".join("data: "+json.dumps(x)+"\n\n" for x in events).encode()
            self.send_response(200);self.send_header("Content-Type","text/event-stream");self.send_header("Content-Length",str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler);threading.Thread(target=server.serve_forever,daemon=True).start()
    try:
        with tempfile.TemporaryDirectory(prefix="sudhir-core-artifact-") as temp:
            home=Path(temp); catalog=json.loads((repo/"codex-rs/models-manager/models.json").read_text())
            seed=next(x for x in catalog["models"] if x["slug"]=="gpt-5.5")
            models=[]
            for slug,window in [("pi-fixture/large",100000),("pi-other/large",100000),("pi-fixture/small",1000)]:
                model={**seed,"slug":slug,"display_name":slug,"context_window":window,"auto_compact_token_limit":int(window*.95),"comp_hash":slug,"use_responses_lite":False};models.append(model)
            (home/"models.json").write_text(json.dumps({"models":models}))
            (home/"config.toml").write_text('model = "pi-fixture/large"\nmodel_provider = "fixture"\nmodel_catalog_json = '+json.dumps(str(home/"models.json"))+'\ncompact_prompt = "SUDHIR_COMPACT summarize history"\ncheck_for_update_on_startup = false\n[shell_environment_policy.filters]\nSUDHIR_CODEX_GATEWAY_TOKEN = "exclude"\n[features]\nenable_request_compression = false\nplugins = false\n[model_providers.fixture]\nname = "Fixture"\nbase_url = "http://127.0.0.1:'+str(server.server_port)+'/v1"\nwire_api = "responses"\nrequires_openai_auth = false\n')
            rpc=RPC(binary,home)
            try:
                identity=rpc.identity
                checks={"private_home":Path(identity["codexHome"]).resolve()==home.resolve()}
                for method,params in [("server/diagnostics",{}),("threadSection/list",{}),("project/list",{}),("model/list",{})]:
                    if baseline and method == "project/list": continue
                    rpc.request(method,params);checks[method]=True
                if legacy_binary:
                    rpc.close();rpc=RPC(legacy_binary,home)
                    assert "0.153.4" in rpc.identity["userAgent"]
                fresh=rpc.request("thread/start",{"model":"pi-fixture/large","cwd":str(home),"approvalPolicy":"never","sandbox":"danger-full-access"});fresh_id=fresh["thread"]["id"]
                rpc.turn(fresh_id,"pi-fixture/large","low pressure first")
                rpc.turn(fresh_id,"pi-other/large","same family different provider and hash")
                assert [x["body"]["model"] for x in captured]==["pi-fixture/large","pi-other/large"]
                checks["provider_hash_switch_without_pressure"]=True
                rpc.close()
                rollouts=list(home.glob("sessions/**/*.jsonl"))
                contexts=[json.loads(line) for file in rollouts for line in file.read_text().splitlines() if json.loads(line).get("type")=="turn_context"]
                assert contexts and any(x.get("payload",{}).get("comp_hash") for x in contexts),"missing persisted comp_hash"
                if legacy_binary:
                    metadata=[json.loads(line) for file in rollouts for line in file.read_text().splitlines() if json.loads(line).get("type")=="session_meta"]
                    assert any(x.get("payload",{}).get("cli_version")=="0.153.4" for x in metadata),"legacy writer not established"
                    checks["retained_0153_rollout_writer"]=True
                if not legacy_binary:
                    for rollout in rollouts:
                        entries = [json.loads(line) for line in rollout.read_text().splitlines()]
                        for entry in entries:
                            if entry.get("type") == "turn_context":
                                entry["payload"]["comp_hash"] = "legacy-preupgrade-hash"
                            elif entry.get("type") == "session_meta":
                                entry["payload"]["cli_version"] = "0.153.4"
                        rollout.write_text("".join(json.dumps(entry) + "\n" for entry in entries))
                    checks["legacy_hash_fixture"] = True
                rpc=RPC(binary,home)
                rpc.request("thread/resume",{"threadId":fresh_id})
                rpc.turn(fresh_id,"pi-fixture/large","resume persisted hash")
                assert len(captured)==3 and captured[-1]["body"]["model"]=="pi-fixture/large"
                checks["persisted_hash_resume_without_compaction"]=True
                captured.clear()
                started=rpc.request("thread/start",{"model":"pi-fixture/large","cwd":str(home),"approvalPolicy":"never","sandbox":"danger-full-access"});tid=started["thread"]["id"]
                rpc.turn(tid,"pi-fixture/large","before switch")
                rpc.turn(tid,"pi-fixture/small","after switch")
                sequence=[x["body"]["model"] for x in captured]
                assert sequence[:3]==["pi-fixture/large","pi-fixture/small","pi-fixture/small"],sequence
                checks["selected_model_pressure"]=True
                rpc.request("thread/resume",{"threadId":tid})
                rpc.turn(tid,"pi-fixture/large","SUDHIR_TOOL_PROBE")
                tool_outputs=[x for r in captured for x in r["body"].get("input",[]) if x.get("type")=="function_call_output"]
                encoded=json.dumps(tool_outputs)
                assert "token_hidden" in encoded and "alias_regular" in encoded,encoded
                assert "fixture-secret" not in json.dumps(captured)
                assert all(tool.get("type") not in {"web_search", "web_search_preview", "image_generation"} for request in captured for tool in request["body"].get("tools", []))
                with tempfile.TemporaryDirectory(prefix="sudhir-alias-artifact-", dir=binary.parent) as aliases:
                    alias = Path(aliases) / "sudhir-core-alias"
                    os.link(binary, alias)
                    alias_env = {**os.environ, "CODEX_HOME": str(home), "HOME": rpc.os_home.name}
                    version = subprocess.check_output([str(alias), "--version"], env=alias_env, text=True)
                    assert "codex-cli" in version and not alias.is_symlink(), version
                    checks["arg0_hard_link"] = True
                checks.update(history_resume=True,gateway_token_exclusion=True,arg0_regular_alias=True,pi_hosted_tool_suppression=True)
                print(json.dumps({"checks":checks,"identity":identity,"request_models":[x["body"]["model"] for x in captured]},indent=2))
            finally: rpc.close()
    finally: server.shutdown();server.server_close()

if __name__=="__main__":
    p=argparse.ArgumentParser();p.add_argument("binary",type=Path);p.add_argument("repo",type=Path);p.add_argument("--baseline",action="store_true");p.add_argument("--legacy-binary",type=Path);a=p.parse_args();verify(a.binary.resolve(),a.repo.resolve(),a.baseline,a.legacy_binary.resolve() if a.legacy_binary else None)
