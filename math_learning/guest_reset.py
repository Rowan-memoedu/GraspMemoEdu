"""Guest-only reset endpoint; identity and feature checks remain server-side."""

from fastapi import Request

from .service import DomainError


def register_guest_reset_routes(app, identity, public_state, Action):
    # No postponed annotations: FastAPI must receive the supplied Action class.
    @app.post('/api/guest/reset')
    def reset(payload: Action, request: Request):
        access, learner = identity(request, 'learn')
        if access['role'] != 'guest':
            raise DomainError('feature_forbidden', '直接重置仅供游客使用。', 403)
        return public_state(learner.reset_progress(payload.model_dump()), access)
