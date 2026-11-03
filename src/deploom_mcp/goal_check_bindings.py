"""Host-pinned check mapping; observations are not reusable goal proof."""
from __future__ import annotations
import hashlib
import base64
import json
from pathlib import Path, PurePosixPath
from deploom_mcp.goal_contract import GoalContractError, canonical_digest
from deploom_mcp.goal_report import _SHA, _plain_under


def validate_check_bindings(profile, required):
    """Legacy engineering profiles have no named evidence; never infer a mapping."""
    if "schemaVersion" not in profile and "checkBindings" not in profile:
        return None
    rows=profile.get("checkBindings")
    if (type(profile.get("schemaVersion")) is not int or profile["schemaVersion"]!=2
            or not isinstance(rows,list) or len(rows)!=len(required)):
        raise GoalContractError("INVALID_CHECK_BINDINGS")
    commands=profile.get("commands")
    if (not isinstance(commands,list) or not commands or any(not isinstance(c,str) or not c.strip() for c in commands)
            or len(set(commands))!=len(commands)):
        raise GoalContractError("INVALID_CHECK_COMMAND_MAPPING")
    seen=set(); indices=set()
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get("checkId"),str) or row["checkId"] not in required or row["checkId"] in seen:
            raise GoalContractError("INVALID_CHECK_BINDINGS")
        seen.add(row["checkId"])
        if row.get("kind")=="command":
            if not {"checkId","kind","commandIndex","commandSha256"}.issubset(row) or set(row)-{"checkId","kind","commandIndex","commandSha256","oracle","normative"}:
                raise GoalContractError("INVALID_CHECK_BINDINGS")
            index=row["commandIndex"]
            if type(index) is not int or not 1<=index<=len(profile["commands"]) or index in indices:
                raise GoalContractError("INVALID_CHECK_COMMAND_MAPPING")
            indices.add(index)
            if row["commandSha256"]!=hashlib.sha256(profile["commands"][index-1].encode("utf-8")).hexdigest():
                raise GoalContractError("CHECK_COMMAND_DIGEST_MISMATCH")
            if "oracle" in row:
                oracle=row["oracle"]
                inline=isinstance(oracle,dict) and oracle.get("mode")=="INLINE_NODE_V1"
                fields={"hostPath","sha256","mode","bodyBytes"} if inline else {"hostPath","sha256"}
                if not isinstance(oracle,dict) or set(oracle)!=fields:
                    raise GoalContractError("INVALID_CHECK_ORACLE")
                if inline and (type(oracle["bodyBytes"]) is not int or not 1<=oracle["bodyBytes"]<=2048):
                    raise GoalContractError("INVALID_INLINE_NODE_ORACLE")
                name=oracle["hostPath"]
                if (not isinstance(name,str) or not name or len(name)>512 or "\\" in name or ":" in name
                        or PurePosixPath(name).is_absolute() or any(s in {"",".",".."} for s in name.split("/"))
                        or not isinstance(oracle["sha256"],str) or not _SHA.fullmatch(oracle["sha256"])):
                    raise GoalContractError("INVALID_CHECK_ORACLE")
        elif row.get("kind")=="core":
            if set(row)-{"normative"}!={"checkId","kind","evidence"} or not isinstance(row["evidence"],str) or row["evidence"] not in {"SOURCE_LOCK_CONTINUITY","OWNED_CLEANUP_CLOSED"}:
                raise GoalContractError("INVALID_CORE_CHECK_BINDING")
        else:
            raise GoalContractError("INVALID_CHECK_BINDINGS")
    if seen!=set(required):raise GoalContractError("INVALID_CHECK_BINDINGS")
    if indices!=set(range(1,len(commands)+1)):
        raise GoalContractError("UNMAPPED_EXECUTION_COMMAND")
    validate_normative_admission(profile,required)
    return rows


def observe_oracles(profile, state_root, project_path):
    """Read pinned host artifacts outside project; missing/drift is unknown, not PASS.

    Byte identity alone does not prove the command used this oracle. The returned
    status is identity evidence only, separate from actual command observation.
    """
    result={}
    for row in profile.get("checkBindings",[]):
        oracle=row.get("oracle")
        if oracle is None:continue
        try:
            path=_plain_under(Path(state_root)/oracle["hostPath"],Path(state_root))
            if path.resolve().is_relative_to(Path(project_path).resolve()):
                raise GoalContractError("CHECK_ORACLE_INSIDE_PROJECT")
            if not path.is_file() or path.stat().st_size>8*1024*1024:
                result[row["checkId"]]="UNAVAILABLE";continue
            body=path.read_bytes();actual=hashlib.sha256(body).hexdigest()
            if oracle.get("mode")=="INLINE_NODE_V1" and actual==oracle["sha256"]:
                if len(body)!=oracle["bodyBytes"] or profile["commands"][row["commandIndex"]-1]!=build_inline_node_command(body):
                    raise GoalContractError("INLINE_ORACLE_COMMAND_MISMATCH")
            result[row["checkId"]]="MATCH" if actual==oracle["sha256"] else "MISMATCH"
        except OSError:
            result[row["checkId"]]="UNAVAILABLE"
    return result


def summarize_check_bindings(profile, confirmed, before, after, command_identities=None):
    rows=profile.get("checkBindings")
    if rows is None:
        return {"mappingStatus":"UNAVAILABLE_LEGACY_PROFILE","checks":[],"goalChecksComplete":False}
    checks=[]
    for row in rows:
        item={"checkId":row["checkId"],"kind":row["kind"],"status":"NOT_VERIFIED"}
        if row["kind"]=="core":
            item.update(evidence=row["evidence"],reason="CORE_OWNER_CLOSURE_REQUIRED")
        else:
            item.update(commandIndex=row["commandIndex"],commandSha256=row["commandSha256"],
                status="COMMAND_OBSERVED_PASS" if row["commandIndex"] in confirmed else "UNKNOWN")
            if "oracle" in row and row["oracle"].get("mode")=="INLINE_NODE_V1":
                ids=(command_identities or {}).get(row["commandIndex"],{})
                bound=(row["commandIndex"] in confirmed and before.get(row["checkId"])=="MATCH"
                    and ids.get("inlineNodeExecutionMode")=="DIRECT_NODE_ARGV_V1"
                    and ids.get("inlineNodeBodySha256")==row["oracle"]["sha256"]
                    and isinstance(ids.get("nodeExecutableSha256"),str) and _SHA.fullmatch(ids["nodeExecutableSha256"]))
                item.update(oracleIdentity="PINNED_HOST_BODY_BOUND" if bound else "UNCONFIRMED",
                    oracleSha256=row["oracle"]["sha256"],oracleCatalogIdentityAfter=after.get(row["checkId"],"UNAVAILABLE"),
                    oracleExecutedBytes="DIRECT_ARGV_BODY_OWNER_OBSERVED" if bound else "NOT_VERIFIED",
                    oracleApplicability="NOT_VERIFIED",oracleExternalInputClosure="NOT_VERIFIED")
                if not bound:item["status"]="UNKNOWN"
            elif "oracle" in row:
                matched=before.get(row["checkId"])==after.get(row["checkId"])=="MATCH"
                item.update(oracleIdentity="MATCH_BEFORE_AND_AFTER" if matched else "UNCONFIRMED",
                    oracleSha256=row["oracle"]["sha256"],oracleApplicability="NOT_VERIFIED",oracleExecutedBytes="NOT_VERIFIED")
                if not matched:item["status"]="UNKNOWN"
        if profile.get("admission") is not None:
            item.update(normativeScope="HOST_PINNED",normativeContractDigest=canonical_digest(row["normative"]),
                arbitraryJSSemantics="NOT_AUTOMATICALLY_PROVEN",externalInputClosure="HOST_DECLARED_NOT_ENFORCED",
                contractFulfillment="SCOPED_OBSERVED" if row["kind"]=="command" and item["status"]=="COMMAND_OBSERVED_PASS" else "NOT_VERIFIED")
        checks.append(item)
    return {"mappingStatus":"HOST_PINNED","checkBindingsDigest":canonical_digest(rows),
        "checks":checks,"goalChecksComplete":False}


def initial_verification_context(profile, identity):
    """Bind initial baseline identity, not a passed baseline or parent checkpoint.

    Legacy goals may omit context and remain engineering-only. The startup
    operator still owns authorization; these hashes cannot supply consent.
    """
    if profile.get("schemaVersion")!=2:
        raise GoalContractError("VERSIONED_CHECK_PROFILE_REQUIRED")
    lineage={"schemaVersion":1,"kind":"INITIAL_BASELINE_IDENTITY_ONLY",
        "identity":{k:identity[k] for k in ("sourceDigest","manifestDigest","lockDigest","toolchainDigest","policyDigest")}}
    return {"profileDigest":canonical_digest(profile),
        "checkBindingsDigest":canonical_digest(profile["checkBindings"]),
        "baselineLineageDigest":canonical_digest(lineage)}


def validate_verification_context(goal, profile, identity):
    admission=validate_normative_admission(profile,goal["requiredChecks"])
    if admission is not None and (not goal.get("verificationContext") or admission["allowedChangeScopeDigest"]!=admission_scope_digest(goal)):
        raise GoalContractError("SCOPED_ADMISSION_GOAL_BINDING_MISMATCH")
    context=goal.get("verificationContext")
    if context is not None and context!=initial_verification_context(profile,identity):
        raise GoalContractError("GOAL_VERIFICATION_CONTEXT_MISMATCH")


def build_inline_node_command(body):
    """Compile pinned host bytes into one canonical direct-argv wrapper."""
    if not isinstance(body,bytes) or not 1<=len(body)<=2048:
        raise GoalContractError("INVALID_INLINE_NODE_ORACLE")
    try:body.decode("utf-8",errors="strict")
    except UnicodeError as exc:raise GoalContractError("INVALID_INLINE_NODE_ORACLE") from exc
    encoded=base64.b64encode(body).decode("ascii")
    code="eval(Buffer.from('"+encoded+"','base64').toString('utf8'))"
    command="node -e "+json.dumps(code,ensure_ascii=True)
    if len(command)>4096:raise GoalContractError("INVALID_INLINE_NODE_ORACLE")
    return command


def admission_scope_digest(goal):
    """No profile/goal hash cycle: bind semantic goal except its profile context."""
    from deploom_mcp.goal_contract import validate_goal
    value=validate_goal(goal)
    return canonical_digest({'kind':'GOAL_CHANGE_SCOPE_V1',
        'goal':{k:v for k,v in value.items() if k!='verificationContext'}})


def validate_normative_admission(profile, required):
    admission=profile.get('admission')
    if admission is None:
        if any('normative' in row for row in profile.get('checkBindings',[]) if isinstance(row,dict)):
            raise GoalContractError('NORMATIVE_CHECK_REQUIRES_ADMISSION_PROFILE')
        return None
    if (not isinstance(admission,dict) or not {'schemaVersion','scope','allowedChangeScopeDigest','optionalAbsenceRules'}.issubset(admission)
            or set(admission)-{'schemaVersion','scope','allowedChangeScopeDigest','optionalAbsenceRules','bundledOwnerRules'}
            or type(admission['schemaVersion']) is not int or admission['schemaVersion']!=1
            or admission['scope'] not in {'ISOLATED_NPM_V2_CANDIDATE','ISOLATED_NPM_V3_CANDIDATE'}
            or not isinstance(admission['allowedChangeScopeDigest'],str) or not _SHA.fullmatch(admission['allowedChangeScopeDigest'])
            or (admission['optionalAbsenceRules']!=[] and admission['scope'] not in {'ISOLATED_NPM_V2_CANDIDATE','ISOLATED_NPM_V3_CANDIDATE'})):
        raise GoalContractError('INVALID_SCOPED_ADMISSION_PROFILE')
    if admission['optionalAbsenceRules']!=[]:
        from deploom_mcp.distribution import resolve_core_dir
        import sys
        sys.path.insert(0,str(resolve_core_dir()))
        from npm_platform_obligations import validate_rules_shape,NpmPlatformEvidenceError
        try:
            validate_rules_shape(admission['optionalAbsenceRules'])
            version=2 if admission['scope']=='ISOLATED_NPM_V2_CANDIDATE' else 3
            if admission['optionalAbsenceRules']['kind']!='NPM_V'+str(version)+'_SRI_PLATFORM_ABSENCE_V1':
                raise NpmPlatformEvidenceError('PROFILE_LOCK_FORMAT_MISMATCH')
        except NpmPlatformEvidenceError as exc:raise GoalContractError('INVALID_SCOPED_PLATFORM_RULES') from exc
    if 'bundledOwnerRules' in admission:
        if admission['scope']!='ISOLATED_NPM_V2_CANDIDATE':
            raise GoalContractError('BUNDLED_OWNER_V2_PROFILE_REQUIRED')
        from deploom_mcp.distribution import resolve_core_dir
        import sys
        sys.path.insert(0,str(resolve_core_dir()))
        from npm_bundle_obligations import validate_bundle_rules_shape,NpmBundleEvidenceError
        try:validate_bundle_rules_shape(admission['bundledOwnerRules'])
        except NpmBundleEvidenceError as exc:raise GoalContractError('INVALID_BUNDLED_OWNER_RULES') from exc
    for row in profile.get('checkBindings',[]):
        normative=row.get('normative')
        if (not isinstance(normative,dict) or set(normative)!={'schemaVersion','purpose','expectedOutcome','allowedInputs'}
                or type(normative['schemaVersion']) is not int or normative['schemaVersion']!=1):
            raise GoalContractError('INVALID_NORMATIVE_CHECK_CONTRACT')
        purpose=normative['purpose']
        if (not isinstance(purpose,dict) or set(purpose)!={'goalRequirementId','subjectPackages'}
                or purpose['goalRequirementId']!=row['checkId'] or row['checkId'] not in required
                or not isinstance(purpose['subjectPackages'],list) or not 1<=len(purpose['subjectPackages'])<=256
                or any(not isinstance(name,str) or not name or len(name)>214 for name in purpose['subjectPackages'])
                or len(set(purpose['subjectPackages']))!=len(purpose['subjectPackages'])):
            raise GoalContractError('INVALID_NORMATIVE_CHECK_PURPOSE')
        expected=normative['expectedOutcome']
        oracle=row.get('oracle',{})
        command=row['kind']=='command'
        expected_wanted={'kind':'PASS','exitCode':0} if command else {'kind':'OWNER_OBSERVED','evidence':row['evidence']}
        if expected!=expected_wanted or (command and type(expected.get('exitCode')) is not int):
            raise GoalContractError('INVALID_NORMATIVE_CHECK_OUTCOME')
        input_oracle=('PINNED_INLINE_BODY' if oracle.get('mode')=='INLINE_NODE_V1'
            else 'SEALED_PROJECT_COMMAND' if command and not oracle else 'CORE_OWNER' if not command else 'UNSUPPORTED')
        wanted={'source':'SEALED_PREPARED_SOURCE','dependencies':'APPROVED_PHYSICAL_TREE',
            'oracle':input_oracle,'externalInputs':'NONE_DECLARED_BY_HOST'}
        declared=normative['allowedInputs'].get('externalInputs') if isinstance(normative['allowedInputs'],dict) else None
        if isinstance(declared,dict):
            if (input_oracle!='SEALED_PROJECT_COMMAND' or not isinstance(profile.get('commands'),list)
                    or profile['commands'][row['commandIndex']-1]!='npm run ci'
                    or set(declared) not in ({'kind','recipeHash'},{'kind','recipeHash','toolCacheRecipeHash'}) or declared.get('kind')!='PINNED_TAP_RCFILE_YAML_RESETTABLE_CHILD_V2'
                    or not isinstance(declared.get('recipeHash'),str) or not _SHA.fullmatch(declared['recipeHash'])
                    or ('toolCacheRecipeHash' in declared and (not isinstance(declared['toolCacheRecipeHash'],str) or not _SHA.fullmatch(declared['toolCacheRecipeHash'])))):
                raise GoalContractError('NORMATIVE_CHECK_INPUT_SCOPE_UNSUPPORTED')
            wanted['externalInputs']=declared
        if input_oracle=='UNSUPPORTED' or normative['allowedInputs']!=wanted:
            raise GoalContractError('NORMATIVE_CHECK_INPUT_SCOPE_UNSUPPORTED')
    return admission
