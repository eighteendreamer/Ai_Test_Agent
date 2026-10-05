"""Public retry endpoints and independent CLIs over actual frozen group objects."""
import hashlib
import json

from src.infrastructure.postgres_runtime import postgres_connect


async def retry_from_production(*,base,root,request,settings,policy,get,post,client,worker,queues,processes,objects,evidence,sends):
    rows=[]
    for label in ["frozen_request","saved_response"]:
        failed_policy={**policy,**({"max_input_bytes":1} if label=="frozen_request" else {"max_response_bytes":1})}
        assert (await client.put(root+"/case-generation-settings",json=failed_policy)).status_code==200
        group=await post(base+"/case-generations",{**request,"creation_key":"production-parent-"+label,
            "include_new_cases":False,"new_case_mode_key":None},202)
        fixed=await get(base+f"/case-generations/{group['id']}/scope")
        await worker("run_case_generation_worker","failed-parent-"+label,"case_generation")
        original=await get(base+f"/case-generations/{group['id']}")
        parent=original["modules"][0]
        assert parent["status"]=="failed" and parent["checkpoint"] is None if label=="frozen_request" else parent["status"]=="failed" and parent["checkpoint"]["response"]
        fresh={**policy,**({"max_input_bytes":1} if label=="saved_response" else {})}
        assert (await client.put(root+"/case-generation-settings",json=fresh)).status_code==200
        path=base+f"/case-generation-tasks/{parent['id']}/retries"
        payload={"creation_key":"production-retry-"+label}
        accepted=await post(path,payload,202)
        assert accepted["retry"]["strategy"]==label and accepted["task"]["id"]!=parent["id"]
        assert accepted["retry"]["config"]["payload"]==fresh
        count=len(sends)
        await worker("run_case_generation_worker","explicit-retry-"+label,"case_generation")
        view=await get(path+"/by-request",creation_key=payload["creation_key"])
        assert view["task"]["status"]=="completed" and len(sends)==count+(label=="frozen_request"),view
        assert await get(base+f"/case-generations/{group['id']}")==original
        assert await get(base+f"/case-generations/{group['id']}/scope")==fixed
        response=await get(base+f"/case-generation-tasks/{view['task']['id']}/response")
        if label=="saved_response":
            assert view["task"]["checkpoint"]["response"]==parent["checkpoint"]["response"]
        reference=view["task"]["checkpoint"]["receipt"]["versions"][0]
        draft=next(item for item in await get("/test-cases/"+reference["case_id"]+"/versions") if item["id"]==reference["version_id"])
        assert len(draft["content_json"]["steps"])==12
        assert draft["generation_metadata"]["incremental_generation"]["retry"]=={"id":view["retry"]["id"],"sha256":view["retry"]["sha256"]}
        for task_id in [parent["id"],view["task"]["id"]]:
            await queues["case_generation"].enqueue({"task_type":"case_generation","project_id":group["project_id"],"task_id":task_id})
            child=processes.start("retry-terminal-"+label+"-"+task_id,"-m","src.cli.run_case_generation_worker","--once","--worker-id","retry-terminal")
            assert await processes.wait(child)==0
        assert len(sends)==count+(label=="frozen_request")
        for ref in [group["scope"],view["task"]["checkpoint"]["response"]]:
            raw=(await objects.read_object_uri(ref["storage_uri"]))["content"]
            assert len(raw)==ref["size_bytes"] and hashlib.sha256(raw).hexdigest()==ref["sha256"]
            (evidence/(ref["sha256"]+".json")).write_bytes(raw)
        with postgres_connect(settings) as conn,conn.cursor() as cur:
            table=settings.database.postgres_api_doc_table
            cur.execute(f"SELECT record FROM {table}_case_gen_retries WHERE id=%s",(view["retry"]["id"],))
            stored=cur.fetchone()["record"]
            assert stored==view["retry"]
        rows.append({"parent_report":original,"scope":fixed,"payload":payload,"view":view,"response":response,
            "pg_retry":stored,"draft":draft,"terminal_redelivery_no_send":True})
    assert len(sends)==6
    for index,item in enumerate(rows):
        invocation=item["scope"]["modules"][0]["generation"]["invocation"]
        assert sends[4+index]["messages"]==[{"role":"system","content":invocation["system_prompt"]},*invocation["messages"]]
    result={"items":rows,"actual_production_api":True,"independent_generation_cli":True,
        "additional_physical_calls":2,"full_business_review":False}
    (evidence/"generation-retries.json").write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result


async def retry_after_restart(*,base,state,get,post,client,evidence):
    for item in state["items"]:
        path=base+f"/case-generation-tasks/{item['view']['retry']['parent_task_id']}/retries"
        assert await get(path+"/by-request",creation_key=item["payload"]["creation_key"])==item["view"]
        assert await post(path,item["payload"],202)==item["view"]
        assert (await client.post(path,json={"creation_key":"disabled-retry-new"})).status_code==503
        assert (await get(path))["items"]==[item["view"]]
        assert await get(base+f"/case-generation-tasks/{item['view']['task']['id']}/response")==item["response"]
    state["actual_api_restart_disabled_dispatch_history"]=True
    (evidence/"generation-retries.json").write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding="utf-8")
