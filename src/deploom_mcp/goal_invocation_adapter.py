"""Code-only consumer for Core invocation observations. No MCP JSON admission path."""
from __future__ import annotations
import copy
import hashlib
import json
import os
from pathlib import Path
import uuid
from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_report import _RUN,_SHA,_plain_under
from deploom_mcp.goal_check_bindings import validate_check_bindings,observe_oracles,summarize_check_bindings

class InvocationObservationCollector:
    def __init__(self,registration,expected_source_key,target_fingerprint,*,engineering_control=False,expected_obligation_plan_digest=None):
        self.registration=registration;self.source_key=expected_source_key;self.target_fingerprint=target_fingerprint
        self.engineering_control=engineering_control;self.events=[];self.invocation_id=None;self.finished=False;self.failed=False
        self.started=False;self.returned=False;self.returned_result=None
        validate_check_bindings(registration.profile,registration.goal["requiredChecks"])
        self.profile_digest=canonical_digest(registration.profile)
        self.expected_obligation_plan_digest=expected_obligation_plan_digest
        from deploom_mcp.goal_execution_registry import configured_state_root
        self.oracle_root=configured_state_root()
        self.oracle_before=observe_oracles(registration.profile,self.oracle_root,registration.project_path)
    def __call__(self,event):
        if (self.failed or self.finished or not isinstance(event,dict) or type(event.get("schemaVersion")) is not int or event["schemaVersion"]!=2
                or type(event.get("sequence")) is not int or event["sequence"]!=len(self.events)+1 or event.get("authority")!="NON_REUSABLE_INVOCATION_OBSERVATION"
                or event.get("reuseAllowed") is not False or event.get("fullAcceptance") is not False
                or event.get("type") not in {"COMMAND_COMPLETED","INVOCATION_RESULT_OBSERVED"}):
            self.failed=True;raise GoalContractError("INVALID_INVOCATION_OBSERVATION_SEQUENCE")
        if not isinstance(event.get("identities"),dict):
            self.failed=True;raise GoalContractError("INVALID_INVOCATION_IDENTITIES")
        if event["type"]=="COMMAND_COMPLETED":
            index=event.get("commandIndex");commands=self.registration.profile["commands"]
            if (type(index) is not int or not 1<=index<=len(commands)
                    or event.get("commandDigest")!=hashlib.sha256(commands[index-1].encode("utf-8")).hexdigest()
                    or any(e["type"]=="COMMAND_COMPLETED" and e["commandIndex"]==index for e in self.events)):
                self.failed=True;raise GoalContractError("INVALID_INVOCATION_COMMAND_SET")
        current=event.get("invocationId")
        if not isinstance(current,str) or not current or (self.invocation_id is not None and current!=self.invocation_id):
            self.failed=True;raise GoalContractError("INVOCATION_ID_MISMATCH")
        self.invocation_id=current;self.events.append(copy.deepcopy(event))
        if event["type"]=="INVOCATION_RESULT_OBSERVED":self.finished=True
    def invoke(self,action):
        """Own the outer call. Callback data can never confirm its own return."""
        if self.started:raise GoalContractError("INVOCATION_ALREADY_STARTED")
        self.started=True
        try:
            latest=observe_oracles(self.registration.profile,self.oracle_root,self.registration.project_path)
            if latest!=self.oracle_before or any(v!="MATCH" for v in latest.values()):
                raise GoalContractError("HOST_CHECK_ORACLE_UNAVAILABLE_OR_CHANGED")
            result=action(self)
            if self.failed or not self.finished:raise GoalContractError("INCOMPLETE_INVOCATION_OBSERVATION")
            self.returned_result=result
            self.returned=True
            return result
        except BaseException:
            self.failed=True
            raise
    def finalize(self,result):
        try:return self._finalize_returned(result)
        except BaseException:
            self.failed=True
            raise
    def _finalize_returned(self,result):
        if canonical_digest(self.registration.profile)!=self.profile_digest:
            raise GoalContractError("PROFILE_CHANGED_DURING_INVOCATION")
        if not self.returned or result is not self.returned_result:
            raise GoalContractError("OUTER_INVOCATION_NOT_RETURNED")
        if self.failed or not self.finished:raise GoalContractError("INCOMPLETE_INVOCATION_OBSERVATION")
        final=self.events[-1];identity={key:str(getattr(result,key,"") or "") for key in (
            "verified_source_snapshot_key","verified_assignment_fingerprint","resolved_state_key","resolved_lockfile_hash","observed_resolved_hash")}
        if final.get("identities")!=identity or final.get("outcome")!=str(getattr(result,"kind","unknown")):
            raise GoalContractError("INVOCATION_RESULT_IDENTITY_MISMATCH")
        if identity["verified_assignment_fingerprint"]!=self.target_fingerprint or identity["verified_source_snapshot_key"]!=self.source_key:
            raise GoalContractError("INVOCATION_BINDING_MISMATCH")
        origin=getattr(result,"materialized_source_origin",{}) or {}
        if not isinstance(origin,dict) or final.get("materializedSourceOrigin",{})!=origin:
            raise GoalContractError("SOURCE_ORIGIN_OBSERVATION_MISMATCH")
        if origin:
            if (type(origin.get("schemaVersion")) is not int or origin["schemaVersion"]!=1
                    or origin.get("authority")!="NON_REUSABLE_CORE_OBSERVATION" or origin.get("status")!="SOURCE_ORIGIN_OBSERVED"
                    or origin.get("fullAcceptance") is not False
                    or origin.get("inputSourceSnapshotKey")!=self.source_key
                    or origin.get("resolvedStateKey")!=identity["resolved_state_key"]
                    or origin.get("resolvedLockfileHash")!=identity["resolved_lockfile_hash"]
                    or any(not isinstance(origin.get(k),str) or not _SHA.fullmatch(origin[k]) for k in
                        ("normalizedInputSourceKey","normalizedPreparedSourceKey","preparedSourceContentKey"))
                    or origin["normalizedInputSourceKey"]!=origin["normalizedPreparedSourceKey"]
                    or origin["normalizedPreparedSourceKey"]!=origin["preparedSourceContentKey"]):
                raise GoalContractError("SOURCE_ORIGIN_OBSERVATION_MISMATCH")
        dependency_observation=getattr(result,"materialized_dependency_obligations",{}) or {}
        if (not isinstance(dependency_observation,dict)
                or final.get("materializedDependencyObligations",{})!=dependency_observation):
            raise GoalContractError("DEPENDENCY_OBLIGATION_OBSERVATION_MISMATCH")
        if self.registration.profile.get("admission") is not None:
            d=dependency_observation;guards=d.get("commandBoundaryGuards")
            if (not origin or not self.expected_obligation_plan_digest
                    or type(d.get("schemaVersion")) is not int or d["schemaVersion"]!=1
                    or d.get("authority")!="NON_REUSABLE_CORE_OBSERVATION"
                    or d.get("status")!="MATERIALIZED_NPM_INSTANCE_OBLIGATIONS_OBSERVED"
                    or d.get("fullAcceptance") is not False or d.get("finalBoundaryObserved") is not True
                    or d.get("sourceOriginObservationDigest")!=canonical_digest(origin)
                    or d.get("obligationPlanDigest")!=self.expected_obligation_plan_digest
                    or d.get("resolvedStateKey")!=identity["resolved_state_key"]
                    or d.get("approvedLockSha256")!=identity["resolved_lockfile_hash"]
                    or type(d.get("unknownCount")) is not int or d["unknownCount"]!=0 or d.get("violations")!=[]
                    or not isinstance(d.get("materializedTreeObservationDigest"),str) or not _SHA.fullmatch(d["materializedTreeObservationDigest"])
                    or not isinstance(guards,list) or len(guards)!=len(self.registration.profile["commands"])):
                raise GoalContractError("DEPENDENCY_OBLIGATION_OBSERVATION_MISMATCH")
            for index,guard in enumerate(guards,1):
                if (not isinstance(guard,dict) or type(guard.get("commandIndex")) is not int or guard["commandIndex"]!=index
                        or guard.get("status")!="PRE_POST_OBSERVED"
                        or guard.get("obligationPlanDigest")!=d["obligationPlanDigest"]
                        or guard.get("materializedTreeObservationDigest")!=d["materializedTreeObservationDigest"]):
                    raise GoalContractError("DEPENDENCY_OBLIGATION_COMMAND_GUARD_MISMATCH")
        confirmed=[];missing=[]
        commands=self.registration.profile["commands"]
        observed=[e for e in self.events if e["type"]=="COMMAND_COMPLETED"]
        if (len(observed)!=len(commands) or any(type(e.get("commandIndex")) is not int for e in observed)
                or sorted(e["commandIndex"] for e in observed)!=list(range(1,len(commands)+1))
                or any(e.get("commandDigest")!=hashlib.sha256(commands[e["commandIndex"]-1].encode("utf-8")).hexdigest() for e in observed)):
            raise GoalContractError("INVALID_INVOCATION_COMMAND_SET")
        for index,command in enumerate(commands,1):
            digest=hashlib.sha256(command.encode()).hexdigest()
            rows=[e for e in self.events if e["type"]=="COMMAND_COMPLETED" and e.get("commandIndex")==index and e.get("commandDigest")==digest]
            e=rows[0] if len(rows)==1 else {};ids=e.get("identities",{})
            if not isinstance(ids,dict):raise GoalContractError("INVALID_INVOCATION_IDENTITIES")
            valid=(e.get("origin")=="EXECUTED_FRESH" and e.get("status")=="PASS" and type(e.get("returnCode")) is int and e["returnCode"]==0
                and e.get("guardsPassed") is True and e.get("supervision")=="guaranteed-tree" and e.get("timedOut") is False and e.get("cancelled") is False
                and ids.get("sourceSnapshotKey")==self.source_key and ids.get("targetOnlyAssignmentFingerprint")==self.target_fingerprint
                and ids.get("resolvedStateKey")==identity["resolved_state_key"] and ids.get("resolvedLockfileHash")==identity["resolved_lockfile_hash"])
            if origin:
                valid=valid and ids.get("sourceOriginObservationDigest")==canonical_digest(origin) and ids.get("preparedSourceContentKey")==origin["preparedSourceContentKey"]
            if self.registration.profile.get("admission") is not None:
                valid=valid and ids.get("dependencyObligationPlanDigest")==dependency_observation["obligationPlanDigest"] and ids.get("materializedTreeObservationDigest")==dependency_observation["materializedTreeObservationDigest"] and ids.get("dependencyPrePostGuardStatus")=="PRE_POST_OBSERVED"
            bound_rows=[r for r in self.registration.profile.get("checkBindings",[]) if r.get("kind")=="command" and r.get("commandIndex")==index]
            for row in bound_rows:
                oracle=row.get("oracle",{})
                if oracle.get("mode")=="INLINE_NODE_V1":
                    valid=valid and ids.get("inlineNodeExecutionMode")=="DIRECT_NODE_ARGV_V1" and ids.get("inlineNodeBodySha256")==oracle["sha256"] and isinstance(ids.get("nodeExecutableSha256"),str) and bool(_SHA.fullmatch(ids["nodeExecutableSha256"]))
            (confirmed if valid else missing).append(index)
        complete=bool(getattr(result,"ok",False) and getattr(result,"kind","")=="passed" and all(identity.values()) and not missing and commands and final.get("beforePrivateWorkspaceDeletion") is True and final.get("status")=="RESULT_OBSERVED_COMPLETE")
        authority=("TEST_DOUBLE_CONTROL_FLOW_ONLY" if self.engineering_control else "CONTROLLED_INVOCATION_OBSERVED" if complete else "CONTROLLED_INVOCATION_INCOMPLETE")
        return {"schemaVersion":1,"runId":self.registration.run_id,"candidateId":self.registration.candidate_id,
            "goalDigest":self.registration.binding["goalDigest"],"bindingDigest":self.registration.binding["bindingDigest"],
            "profileDigest":canonical_digest(self.registration.profile),"invocationId":self.invocation_id,"identities":identity,
            "materializedSourceOrigin":origin or None,"materializedDependencyObligations":dependency_observation or None,
            "outerInvocationReturned":True,"cleanupCompletedWithoutException":True,"observationComplete":complete,
            "targetOnlyFingerprint":self.target_fingerprint,"fingerprintScope":"TARGET_MAP_ONLY",
            "verificationAuthority":authority,"verification":"NOT_VERIFIED","fullAcceptance":False,"executionReady":False,
            "commandChecks":{"required":len(commands),"freshConfirmed":confirmed,"unconfirmed":missing},
            "namedCheckObservations":summarize_check_bindings(self.registration.profile,confirmed,self.oracle_before,
                observe_oracles(self.registration.profile,self.oracle_root,self.registration.project_path),
                {e["commandIndex"]:e["identities"] for e in observed}),
            "metadataNecessityCheck":"UNKNOWN","heldRootCheck":"NOT_VERIFIED",
            "evidenceDigest":canonical_digest(self.events),"historicalTrialOnly":True,"currentWorkspaceVerified":False,
            "engineeringControl":self.engineering_control}
    def save_observation(self,result,state_root: Path):
        record=self.finalize(result)
        if self.engineering_control:raise GoalContractError("ENGINEERING_CONTROLS_CANNOT_PUBLISH_PROJECT_OBSERVATIONS")
        if not all(_RUN.fullmatch(value) for value in [record["runId"],record["candidateId"]]):raise GoalContractError("INVALID_OBSERVATION_ID")
        directory=_plain_under(state_root/"execution-observations",state_root)
        if directory.resolve().is_relative_to(Path(self.registration.project_path).resolve()):raise GoalContractError("OBSERVATION_STORE_INSIDE_PROJECT")
        directory.mkdir(parents=True,exist_ok=True)
        path=directory/(canonical_digest({"runId":record["runId"],"candidateId":record["candidateId"]})+".json")
        lock=path.with_suffix(".lock")
        try:
            with lock.open("x",encoding="utf-8") as stream:stream.write(str(os.getpid()))
        except FileExistsError as exc:raise GoalContractError("OBSERVATION_PUBLICATION_IN_FLIGHT") from exc
        temporary=path.with_name(path.name+"."+uuid.uuid4().hex+".tmp")
        try:
            if path.exists():raise GoalContractError("OBSERVATION_ALREADY_RECORDED")
            with temporary.open("x",encoding="utf-8",newline="\n") as stream:
                json.dump(record,stream,sort_keys=True);stream.write("\n");stream.flush();os.fsync(stream.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
            lock.unlink(missing_ok=True)
        return path
