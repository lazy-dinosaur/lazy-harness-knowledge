"""Private inherited-pipe review capability. No permission is accepted from query JSON."""
import json
import os
from answer_review_adapter import sdk_policy


def stdio_policy(host, provider, model):
    # Descriptors are supplied by the trusted extension constructor's fixed spawn.
    # Opening these also rejects a selector without the actual private host port.
    output = os.fdopen(os.dup(3), 'w', encoding='utf-8')
    reply = os.fdopen(os.dup(4), 'r', encoding='utf-8')
    used = False

    def complete(request):
        nonlocal used
        if used:
            raise ValueError('one search review only')
        used = True
        output.write(json.dumps(request, ensure_ascii=False, allow_nan=False) + '\n')
        output.flush()
        line = reply.readline(512 * 1024 + 1)
        if not line.endswith('\n') or len(line.encode()) > 512 * 1024:
            raise ValueError('review IPC response invalid')
        return json.loads(line)

    return sdk_policy(host, provider, model, complete)
