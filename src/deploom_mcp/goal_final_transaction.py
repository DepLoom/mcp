"""Recoverable exact source delivery for one explicitly authorized final attempt.

No checkpoint or current-workspace PASS is manufactured by source delivery.
The same durable intent and original deadline survive process restart. A writer
lock excludes competing DepLoom writers; source checks observe boundaries and
are not an OS compare-and-swap guarantee against arbitrary external editors.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import time

from deploom_mcp.goal_contract import GoalContractError,canonical_digest
from deploom_mcp.goal_execution_registry import (configured_state_root,
    load_host_registration,OPERATION)
from deploom_mcp.goal_report import _plain_under
from deploom_mcp.goal_delta_trial import (_core,_inventory,_proposal_artifacts,
    describe_exact_delta,propose_exact_delta,_json)
from deploom_mcp.goal_final_authorization import consume_final_grant


def _sha(raw):return hashlib.sha256(raw).hexdigest()


def assert_partial_source(before,actual,files):
    """Only approved preimages or postimages; every other source row identical."""
    planned={row['path']:row for row in files}
    if len(planned)!=len(files) or set(before)!=set(actual):
        raise GoalContractError('FOREIGN_OR_STALE_WORKSPACE_EDIT')
    for name,old in before.items():
        current=actual[name];row=planned.get(name)
        if row is None:
            if current!=old:raise GoalContractError('FOREIGN_OR_STALE_WORKSPACE_EDIT')
        else:
            if (old.get('sha256')!=row['beforeSha256'] or old.get('kind')!='file'
                    or current.get('kind')!='file'
                    or current.get('executable')!=old.get('executable')
                    or current.get('sha256') not in {row['beforeSha256'],row['afterSha256']}):
                raise GoalContractError('FOREIGN_OR_STALE_WORKSPACE_EDIT')
    if not set(planned).issubset(before):raise GoalContractError('FINAL_SOURCE_FILE_SCOPE_MISMATCH')


class FinalApplyTransaction:
    def __init__(self,grant,*,fault_cutpoint=None):
        self.context=consume_final_grant(grant)
        if self.context.read_only:raise GoalContractError('READ_ONLY_FINAL_CONTEXT_CANNOT_MUTATE')
        self.registration=self.context.registration
        self.core=_core();self.root=configured_state_root().resolve()
        self.project=_plain_under(Path(self.registration.project_path),Path(self.registration.project_path)).resolve()
        self.key=canonical_digest({'runId':self.registration.run_id,'candidateId':self.registration.candidate_id,'attemptId':self.context.attempt_id})
        self.directory=_plain_under(self.root/'final-apply-attempts'/self.key,self.root)
        self.project_state=_plain_under(self.root/'final-goal-projects'/canonical_digest({'project':str(self.project)}),self.root)
        self.journal_path=self.directory/'transaction.json';self.record=None;self.lease=None
        self.fault_cutpoint=fault_cutpoint

    def __enter__(self):
        from run_writer_lock import acquire
        self.check_permission();self.directory.mkdir(parents=True,exist_ok=True)
        self.project_state.mkdir(parents=True,exist_ok=True)
        self.lease=acquire(self.project_state/'writer.json','goal-final-'+self.key,
            lambda:False,timeout=min(5,self.remaining()))
        try:
            if self.journal_path.exists():self.load()
            return self
        except BaseException:
            self.__exit__(None,None,None);raise

    def __exit__(self,*_):
        from run_writer_lock import release
        release(self.project_state/'writer.json',self.lease);self.lease=None

    def remaining(self):
        deadline=self.registration.deadline
        if self.record is not None:deadline=min(deadline,self.record['deadline'])
        value=deadline-time.time()
        if value<=0:raise GoalContractError('FINAL_ATTEMPT_DEADLINE_EXCEEDED')
        return max(1,int(value))

    def check_permission(self):
        self.remaining()
        current=load_host_registration(self.registration.run_id,self.registration.candidate_id,
            self.registration.binding['bindingDigest'],OPERATION)
        if current!=self.registration:raise GoalContractError('FINAL_REGISTRATION_CHANGED')

    def save(self,phase=None):
        from verification_stage_checkpoint import write_atomic
        self.remaining()
        if phase is not None:self.record['phase']=phase
        self.record['spentSeconds']=max(0,time.time()-self.record['startedAt'])
        payload=self.record
        write_atomic(self.journal_path,{'schemaVersion':1,'payload':payload,'sha256':canonical_digest(payload)})

    def cut(self,name):
        if self.fault_cutpoint is not None:self.fault_cutpoint(name)

    def load(self):
        envelope=_json(self.journal_path.read_bytes(),8*1024*1024)
        if (set(envelope)!={'schemaVersion','payload','sha256'} or type(envelope['schemaVersion']) is not int
                or envelope['schemaVersion']!=1 or not isinstance(envelope['payload'],dict)
                or canonical_digest(envelope['payload'])!=envelope['sha256']):
            raise GoalContractError('FINAL_TRANSACTION_JOURNAL_UNPROVEN')
        value=envelope['payload'];reg=self.registration
        if (value.get('schemaVersion')!=1 or value.get('attemptKey')!=self.key
                or value.get('attemptId')!=self.context.attempt_id
                or value.get('authorizationDigest')!=self.context.authorization_digest
                or value.get('registryDigest')!=reg.manifest_sha256
                or value.get('bindingDigest')!=reg.binding['bindingDigest']
                or value.get('profileDigest')!=canonical_digest(self.context.final_profile)
                or value.get('project')!=str(self.project)
                or value.get('budgetSeconds')!=self.context.budget_seconds
                or type(value.get('startedAt')) not in (int,float)
                or type(value.get('deadline')) not in (int,float)
                or value['deadline']!=min(reg.deadline,value['startedAt']+self.context.budget_seconds)):
            raise GoalContractError('FINAL_TRANSACTION_IDENTITY_CHANGED')
        self.record=value;self.remaining();self.check_permission()

    def baseline(self):
        snapshot=self.core.open_source_snapshot(self.directory/'baseline-source',
            expected_key=self.registration.delta_authorization['sourceSnapshotKey'],timeout_seconds=self.remaining())
        if snapshot.project_relative.as_posix() not in {'.',''}:
            raise GoalContractError('FINAL_PROJECT_CAPTURE_SCOPE_UNSUPPORTED')
        return snapshot

    def start(self,candidate_bytes):
        if self.record is not None:
            snapshot=self.baseline()
            proposal=describe_exact_delta(self.registration,snapshot,*candidate_bytes)
        else:
            self.check_permission()
            # Existing durable source owner; an orphan baseline capture is not
            # permission and no source file is touched before intent publication.
            with self.core.verification_source_snapshot_scope(self.project,
                    timeout_seconds=self.remaining(),strict_cleanup=True) as initial:
                if initial.capture_root.resolve()!=self.project or initial.project_relative.as_posix() not in {'.',''}:
                    raise GoalContractError('FINAL_PROJECT_CAPTURE_SCOPE_UNSUPPORTED')
                proposal=propose_exact_delta(self.registration,initial,*candidate_bytes)
                destination=self.directory/'baseline-source'
                if destination.exists():snapshot=self.core.open_source_snapshot(destination,expected_key=initial.key,timeout_seconds=self.remaining())
                else:snapshot=self.core.persist_source_snapshot(initial,destination,timeout_seconds=self.remaining())
            before=_inventory(self.core,snapshot.root,snapshot.input_policy,self.remaining())
            actual=_inventory(self.core,self.project,snapshot.input_policy,self.remaining())
            if actual!=before:raise GoalContractError('FOREIGN_OR_STALE_WORKSPACE_EDIT')
            pointer=self.project_state/'current.json'
            incumbent=_json(pointer.read_bytes(),128*1024) if pointer.exists() else None
            from deploom_mcp.goal_final_checkpoint import validate_existing_incumbent
            validate_existing_incumbent(incumbent,self.project_state,self.root,self.core)
            now=time.time();reg=self.registration
            self.record={'schemaVersion':1,'attemptKey':self.key,'attemptId':self.context.attempt_id,
                'runId':reg.run_id,'candidateId':reg.candidate_id,'project':str(self.project),
                'authorizationDigest':self.context.authorization_digest,'registryDigest':reg.manifest_sha256,
                'bindingDigest':reg.binding['bindingDigest'],'profileDigest':canonical_digest(self.context.final_profile),
                'startedAt':now,'deadline':min(reg.deadline,now+self.context.budget_seconds),
                'budgetSeconds':self.context.budget_seconds,'spentSeconds':0,'phase':'INTENT_DURABLE',
                'baselineSourceKey':snapshot.key,'baselineInventoryDigest':canonical_digest(before),
                'candidateDigest':proposal.candidate_digest,'deltaDigest':proposal.delta_digest,
                'files':proposal.descriptor['files'],'incumbent':incumbent,'completedSourceFiles':[],'windowsMoves':{},
                'currentWorkspaceVerified':False,'checkpointStatus':'NOT_PUBLISHED','fullAcceptance':False}
            self.save();self.cut('INTENT_DURABLE')
        if (proposal.candidate_digest!=self.registration.delta_authorization['candidateDigest']
                or proposal.delta_digest!=self.registration.delta_authorization['deltaDigest']
                or proposal.candidate_digest!=self.record['candidateDigest']
                or proposal.delta_digest!=self.record['deltaDigest']
                or proposal.descriptor['files']!=self.record['files']):
            raise GoalContractError('FINAL_CANDIDATE_BYTES_CHANGED')
        self.proposal=proposal;self.snapshot=snapshot
        self.guard_source()
        return proposal

    def guard_source(self):
        self.check_permission()
        snapshot=self.baseline()
        before=_inventory(self.core,snapshot.root,snapshot.input_policy,self.remaining())
        if canonical_digest(before)!=self.record['baselineInventoryDigest']:
            raise GoalContractError('FINAL_BASELINE_SNAPSHOT_CHANGED')
        actual=_inventory(self.core,self.project,snapshot.input_policy,self.remaining())
        from deploom_mcp.goal_windows_file_swap import observe_swap
        comparison=dict(actual)
        for name,move in self.record['windowsMoves'].items():
            self.validate_move(name,move)
            state=observe_swap(move,deadline=self.record['deadline'])
            if state=='OLD_MOVED_TO_OWNED_BACKUP':
                if name in actual:raise GoalContractError('FINAL_SOURCE_RECOVERY_UNPROVEN')
                # This is an authorized delivery intermediate, not a check PASS.
                comparison[name]=before[name]
        assert_partial_source(before,comparison,self.record['files'])
        return actual

    def validate_move(self,name,move):
        rows={row['path']:row for row in self.record['files']}
        if name not in rows:raise GoalContractError('FINAL_SOURCE_MOVE_SCOPE_MISMATCH')
        index=[row['path'] for row in self.record['files']].index(name);row=rows[name]
        if (move['target']!=str(self.project/name)
                or move['staged']!=str(self.directory/'payloads'/(str(index)+'.bin'))
                or move['backup']!=str(self.directory/'source-preimages'/(str(index)+'.bin'))
                or move['before']['sha256']!=row['beforeSha256']
                or move['after']['sha256']!=row['afterSha256']
                or move['deadline']!=self.record['deadline']):
            raise GoalContractError('FINAL_SOURCE_MOVE_BINDING_MISMATCH')

    def deliver_source(self):
        from deploom_mcp.goal_windows_file_swap import prepare_swap,apply_swap,observe_swap
        if self.record['phase'] in {'TERMINAL_PASS','POINTER_COMMITTED'}:
            raise GoalContractError('FINAL_TERMINAL_ATTEMPT_READ_ONLY')
        if self.record['phase'] in {'DEPENDENCIES_STAGED','DEPENDENCIES_READY','LIVE_VERIFIED','SNAPSHOT_DURABLE'}:
            self.guard_source()
            if any(observe_swap(move,deadline=self.record['deadline'])!='DELIVERED' for move in self.record['windowsMoves'].values()):
                raise GoalContractError('FINAL_SOURCE_DELIVERY_INCOMPLETE')
            return
        raw_files=dict(_proposal_artifacts(self.proposal))
        payloads=self.directory/'payloads';payloads.mkdir(exist_ok=True)
        preimages=self.directory/'source-preimages';preimages.mkdir(exist_ok=True)
        for index,row in enumerate(self.record['files']):
            self.guard_source();name=row['path'];target=_plain_under(self.project/name,self.project)
            raw=raw_files[name]
            if _sha(raw)!=row['afterSha256']:raise GoalContractError('FINAL_SOURCE_POSTIMAGE_CHANGED')
            move=self.record['windowsMoves'].get(name)
            if move is None:
                if _sha(target.read_bytes())!=row['beforeSha256']:
                    raise GoalContractError('FOREIGN_OR_STALE_WORKSPACE_EDIT')
                staged=payloads/(str(index)+'.bin')
                if not staged.exists():
                    with staged.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                if _sha(staged.read_bytes())!=row['afterSha256']:raise GoalContractError('FINAL_STAGED_POSTIMAGE_CHANGED')
                shutil.copymode(target,staged)
                move=prepare_swap(target,staged,preimages/(str(index)+'.bin'),
                    before_sha256=row['beforeSha256'],after_sha256=row['afterSha256'],deadline=self.record['deadline'])
                self.record['windowsMoves'][name]=move
                self.save();self.cut('SOURCE_MOVE_PLAN_'+str(index+1))
            self.validate_move(name,move)
            apply_swap(move,deadline=self.record['deadline'],
                cutpoint=lambda point:self.cut('SOURCE_'+str(index+1)+'_'+point))
            if observe_swap(move,deadline=self.record['deadline'])!='DELIVERED':
                raise GoalContractError('FINAL_SOURCE_DELIVERY_INCOMPLETE')
            self.guard_source()
            if name not in self.record['completedSourceFiles']:self.record['completedSourceFiles'].append(name)
            self.save();self.cut('SOURCE_FILE_'+str(index+1))
        self.guard_source()
        if any(_sha((self.project/row['path']).read_bytes())!=row['afterSha256'] for row in self.record['files']):
            raise GoalContractError('FINAL_SOURCE_DELIVERY_INCOMPLETE')
        self.save('SOURCE_DELIVERED');self.cut('SOURCE_DELIVERED')
