"""Atomic local events for the floating Jev widget. Never stores credentials."""
import json
import os
from pathlib import Path
import tempfile
import time
import uuid

WIDGET_EVENTS = Path(f'/private/tmp/rekordbox-bridge-{os.getuid()}/widget/evidence/jev-events')

DEFAULT_EVENTS = Path(__file__).resolve().parents[1]/'evidence/jev-events'


def write_event(event, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=directory,
                                         prefix='.event-', suffix='.tmp', delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(event, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, directory/(event['id']+'.json'))
        if directory.resolve() == DEFAULT_EVENTS.resolve():
            try:
                WIDGET_EVENTS.mkdir(parents=True,exist_ok=True,mode=0o700)
                write_event(event,WIDGET_EVENTS)
            except OSError:
                pass
    finally:
        if temporary and temporary.exists():
            temporary.unlink()


def evaluate_recorded(prepared, api_key=None, *, directory=DEFAULT_EVENTS, evaluator=None):
    if evaluator is None:
        from jev_decisions import evaluate
        evaluator = evaluate
    event = {'schema_version': 1, 'id': str(uuid.uuid4()), 'started_at': time.time(),
             'status': 'pending', 'request': prepared, 'result': None, 'error': None}
    # Logging is observational: a disk failure must not replace a provider result.
    def persist():
        try:
            write_event(event, directory)
        except OSError:
            pass
    persist()
    try:
        result = evaluator(prepared, api_key=api_key)
        event.update(status='answered' if result.get('inference_performed') else 'not_sent', result=result)
        return result
    except KeyboardInterrupt:
        event.update(status='cancelled', error='Proef gestopt door de gebruiker.')
        raise
    except Exception as error:
        message = str(error)
        secret = api_key or os.environ.get('TYPESAFE_API_KEY')
        if secret:
            message = message.replace(secret, '[verborgen]')
        event.update(status='error', error=message)
        raise
    finally:
        event['finished_at'] = time.time()
        persist()
