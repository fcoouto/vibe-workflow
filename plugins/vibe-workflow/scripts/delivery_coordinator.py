"""Callable host boundary; decision references bind independently verified authority.

The host verifies user authority and actual native/provider observations before
constructing this owner. This module has no JSON CLI or default trusted source.
It cannot qualify enrollment, mint an AcceptedScope, or create a worktree.
"""
from copy import deepcopy

from runtime import delivery, evidence, state


class DeliveryOperationOwner:
    def __init__(self, verified_decision, *, native, repository, gate):
        evidence._bounded(verified_decision)
        state._fields(verified_decision, ('action', 'request_digest', 'accepted_scope_digest', 'reference'))
        for key in ('request_digest', 'accepted_scope_digest'):
            state._canonical_hash(verified_decision[key])
        state._text(verified_decision['action'], 'delivery action', 64)
        state._text(verified_decision['reference'], 'verified coordinator reference')
        self._decision = deepcopy(verified_decision)
        self.native, self.repository, self.gate = native, repository, gate

    def authorize(self, scope, action, request):
        decision = self._decision
        delivery._require(isinstance(scope, state.AcceptedScope)
            and scope.accepted_scope_digest == decision['accepted_scope_digest']
            and action == decision['action'] and evidence._digest(request) == decision['request_digest'],
            'Delivery operation differs from independently verified scope or decision')
        if action in {'ready', 'merge', 'settle'}:
            with state._database(scope) as db:
                task = evidence._task(db, scope, request, revision=False)
                state._ordinary_capability(scope, task,
                    'delivery-ready' if action == 'ready' else 'delivery-merge')

    def run(self, scope, action: str, request: dict) -> dict:
        self.authorize(scope, action, request)
        return delivery.operate(scope, action, request, owner=self)
