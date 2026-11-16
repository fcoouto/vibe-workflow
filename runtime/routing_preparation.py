"""Internal request-scoped acquisition and pure preview; neither is a routing protocol operation."""
import importlib


def _selection(service, body):
    from scripts import routing as command
    command.require(type(body) is dict and {'assignment', 'job'} <= set(body) <= set(command.FIELDS['prepare'][1]) - {
        'observations'} | {'assignment', 'job'}, 'invalid_request')  # `workflow` is ignored: nothing is bound
    command.require(service.project is not None, 'project_required')
    return service._select(body)


def preview(service, body):
    """Read one request's shortlist, with no workflow binding, ledger, windows, reader, admission or write."""
    from scripts import routing as command
    try:
        selected = _selection(service, body)
        cut = importlib.import_module(command.COLLECTOR).shortlist(
            str(service.project), selected['assignment'], settings_path=service.settings_path,
            **{key: selected[key] for key in service.SELECTION})
    except command.Blocked as error:
        return dict(error.detail, state='blocked', reason=error.reason)
    except Exception:  # noqa: BLE001 - unreadable rules or qualifications: fixed reason, never a traceback
        return dict(state='blocked', reason='observations_unavailable')
    return dict(state='previewed', mode=selected['mode'], ranking=selected['ranking'], shortlist=cut['shortlist'],
                routes=[route['route'] for route in cut['routes']], excluded=cut['excluded'],
                routes_version=cut['routes_version'],
                unmeasured=[route['route'] for route in cut['routes'] if route.get('demand') is None])  # fallback only


def acquire_billing(service, body):
    """Acquire before an external preview/served preparation; the later preview must still verify every fact."""
    from scripts import routing as command
    if service.serving:
        return
    try:
        selected = _selection(service, body)
        importlib.import_module(command.COLLECTOR).acquire_billing(
            str(service.project), selected['assignment'], settings_path=service.settings_path,
            **{key: selected[key] for key in service.SELECTION})
    except Exception:  # noqa: BLE001 - acquisition grants nothing; the ordinary preview retains its refusals
        return
