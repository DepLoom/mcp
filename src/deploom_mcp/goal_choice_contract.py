"""Finite-domain parent intent; exact v1/v2 remains a separate cheap contract."""
from __future__ import annotations
import copy
from jsonschema import Draft202012Validator
from deploom_mcp.goal_contract import GOAL_SCHEMA, GOAL_SCHEMA_V2, GoalContractError, validate_goal

OBJECTIVE='PRIMARY_VERSION_THEN_MIN_CHANGES_V1'

def validate_choice_goal(goal):
    schema=copy.deepcopy(GOAL_SCHEMA_V2 if goal.get('allowSourceRepair') else GOAL_SCHEMA)
    schema['properties']['schemaVersion']={'const':3}
    schema['properties']['goalKind']={'const':'coupled-maintenance'}
    schema['required'].remove('exactTargets');schema['properties'].pop('exactTargets')
    fields={
        'candidateDomains':{'type':'object','minProperties':1,'maxProperties':32,
            'propertyNames':GOAL_SCHEMA['properties']['exactTargets']['propertyNames'],
            'additionalProperties':{'type':'array','minItems':1,'maxItems':32,'uniqueItems':True,
                'items':GOAL_SCHEMA['properties']['exactTargets']['additionalProperties']}},
        'metadataDigest':GOAL_SCHEMA['properties']['policyDigest'],
        'nodeVersion':GOAL_SCHEMA['properties']['exactTargets']['additionalProperties'],
        'objective':{'type':'object','additionalProperties':False,'required':['kind','primaryPackage','maxMajorChanges'],
            'properties':{'kind':{'const':OBJECTIVE},'primaryPackage':{'type':'string'},
                'maxMajorChanges':{'type':'integer','minimum':0,'maximum':32}}}}
    schema['properties'].update(fields);schema['required'].extend(fields)
    errors=list(Draft202012Validator(schema).iter_errors(goal))
    if errors:raise GoalContractError('INVALID_COUPLED_GOAL',errors[0].message)
    value=copy.deepcopy(goal)
    if set(value['candidateDomains'])!=set(value['mutablePackages']):raise GoalContractError('COUPLED_SCOPE_MISMATCH')
    if value['objective']['primaryPackage'] not in value['candidateDomains']:raise GoalContractError('INVALID_PRIMARY_PACKAGE')
    child={k:v for k,v in value.items() if k not in fields}
    child.update(schemaVersion=2 if value['allowSourceRepair'] else 1,goalKind='functional-maintenance',
        exactTargets={n:vs[0] for n,vs in value['candidateDomains'].items()})
    checked=validate_goal(child)
    for key in ('mutablePackages','immutablePackages','excludedPackages','requiredChecks','sourceRepairAllowlist'):
        if key in checked:value[key]=checked[key]
    for name in value['candidateDomains']:value['candidateDomains'][name]=sorted(value['candidateDomains'][name])
    return value

def exact_child(parent,assignment,policy_digest):
    drop={'candidateDomains','metadataDigest','nodeVersion','objective'}
    child={k:copy.deepcopy(v) for k,v in parent.items() if k not in drop}
    child.update(schemaVersion=2 if parent['allowSourceRepair'] else 1,goalKind='functional-maintenance',
        exactTargets={n:assignment[n] for n in parent['mutablePackages']},policyDigest=policy_digest)
    return validate_goal(child)

def validate_choice_response(payload,parent):
    """Validate worker envelope too; PLANNING_ONLY never becomes an apply grant."""
    from deploom_mcp.goal_contract import bind_goal,canonical_digest
    intent=payload.get('derivedExactIntent')
    fields={'kind','parentGoalDigest','selectionDigest','goal','binding','policy','authority','verification','executionReady'}
    if 'verificationContext' in parent:fields.add('executionProfile')
    if (payload.get('modelStatus')!='OPTIMAL_DECLARED_ROOT_IR_ONLY'
            or any(payload.get(k)!=v for k,v in {'fullTreeStatus':'NOT_EVALUATED','installationStatus':'NOT_RUN','runtimeStatus':'NOT_RUN'}.items())
            or not isinstance(intent,dict) or set(intent)!=fields
            or intent['kind']!='DERIVED_EXACT_EXECUTION_INTENT_V1'
            or intent['authority']!='PLANNING_ONLY' or intent['verification']!='NOT_VERIFIED' or intent['executionReady'] is not False
            or intent['selectionDigest']!=payload.get('selectionEvidenceDigest')
            or not isinstance(payload.get('assignment'),dict)
            or not isinstance(intent['policy'],dict)):
        raise GoalContractError('INVALID_COUPLED_WORKER_PROTOCOL')
    wrapper=intent['policy'].get('coupledChoice',{})
    from deploom_mcp.goal_report import _SHA
    if not isinstance(wrapper,dict) or not isinstance(wrapper.get('issuanceDigest'),str) or not _SHA.fullmatch(wrapper['issuanceDigest']):
        raise GoalContractError('INVALID_COUPLED_WORKER_PROTOCOL')
    parent_binding=payload['binding']
    expected_child=exact_child(parent,payload['assignment'],canonical_digest(intent['policy']))
    if 'verificationContext' in parent:
        from deploom_mcp.goal_choice_profile import recover_parent_profile,rebind_profile
        original=recover_parent_profile(parent,intent['executionProfile'])
        expected_child,expected_profile=rebind_profile(parent,parent_binding['identity'],expected_child,
            {**parent_binding['identity'],'policyDigest':expected_child['policyDigest']},original)
        if expected_profile!=intent['executionProfile']:raise GoalContractError('INVALID_COUPLED_WORKER_PROTOCOL')
    if (intent['parentGoalDigest']!=parent_binding['goalDigest']
            or wrapper!={'schemaVersion':1,'selectionDigest':intent['selectionDigest'],
                'parentGoalDigest':parent_binding['goalDigest'],'parentBindingDigest':parent_binding['bindingDigest'],
                'metadataDigest':parent['metadataDigest'],'issuanceDigest':wrapper['issuanceDigest']}
            or intent['goal']!=expected_child
            or intent['binding']!=bind_goal(intent['goal'],{**parent_binding['identity'],'policyDigest':intent['goal']['policyDigest']})):
        raise GoalContractError('INVALID_COUPLED_WORKER_PROTOCOL')
