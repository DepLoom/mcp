"""Pure validation of actual Core workspace observations; no authority/grant."""
import hashlib,json
from deploom_mcp.goal_contract import canonical_digest

def workspace_profile_complete(profile,expected,observation,plan,project):
    try:
        digest=canonical_digest(profile);o=observation
        tree=o['materializedDependencyObligations'];origin=o['materializedSourceOrigin']
        if (type(o.get('schemaVersion')) is not int or o['schemaVersion']!=1
                or not isinstance(o.get('freshCommandInvocationId'),str) or not o['freshCommandInvocationId']
                or o['status']!='CURRENT_WORKSPACE_PROFILE_OBSERVED' or o['executionScope']!='REGISTERED_CURRENT_WORKSPACE'
                or o['authority']!='NON_REUSABLE_CORE_CURRENT_WORKSPACE_OBSERVATION'
                or o['sourceSnapshotKey']!=expected or o['workspacePathDigest']!=canonical_digest(str(project))
                or o['finalVerificationProfileDigest']!=digest or o['ownedCleanupConfirmed'] is not True
                or o['sourceSnapshotCleanupClosed'] is not True or o['finalBoundaryObserved'] is not True
                or o['fullAcceptance'] is not False or o['checkpointPublished'] is not False
                or origin['inputSourceSnapshotKey']!=expected
                or o['deliveredSourceObservationDigest']!=canonical_digest(origin)
                or tree['sourceOriginObservationDigest']!=canonical_digest(origin)
                or tree['approvedLockSha256']!=plan['approvedLockSha256']
                or o['resolvedLockfileHash']!=plan['approvedLockSha256']
                or tree['obligationPlanDigest']!=plan['obligationPlanDigest']
                or tree['resolvedStateKey']!=o['resolvedStateKey']
                or type(tree['unknownCount']) is not int or tree['unknownCount']!=0 or tree['violations']!=[]
                or o['liveMaterializedTreeObservationDigest']!=tree['materializedTreeObservationDigest']):return False
        rows=o['commandObservations']
        if len(rows)!=len(profile['commands']):return False
        for index,(command,row) in enumerate(zip(profile['commands'],rows),1):
            if (type(row['commandIndex']) is not int or row['commandIndex']!=index
                    or row['commandDigest']!=hashlib.sha256(command.encode()).hexdigest()
                    or row['origin']!='EXECUTED_FRESH' or row['status']!='PASS'
                    or type(row['returnCode']) is not int or row['returnCode']!=0
                    or row['supervision']!='guaranteed-tree' or row['commandBoundaryGuard']!='PRE_POST_OBSERVED'
                    or row['freshCommandInvocationId']!=o['freshCommandInvocationId']
                    or row['finalVerificationProfileDigest']!=digest or row['sourceSnapshotKey']!=expected
                    or row['resolvedStateKey']!=o['resolvedStateKey']
                    or row['obligationPlanDigest']!=plan['obligationPlanDigest']
                    or row['materializedTreeObservationDigest']!=tree['materializedTreeObservationDigest']):return False
        for binding in profile['checkBindings']:
            if binding.get('oracle'):
                row=rows[binding['commandIndex']-1];inline=row['inlineExecution']
                if (not inline or inline['bodySha256']!=binding['oracle']['sha256'] or inline['executionMode']!='DIRECT_NODE_ARGV_V1'
                        or inline.get('nodeExecutableSha256')!=origin['toolchain']['nodeSha256']
                        or not isinstance(inline.get('argv'),list) or len(inline['argv'])!=3
                        or inline['argv'][1]!='-e'
                        or inline['argv'][2]!=json.loads(profile['commands'][binding['commandIndex']-1][len('node -e '):])):return False
        return True
    except (KeyError,TypeError,ValueError,AttributeError):return False

