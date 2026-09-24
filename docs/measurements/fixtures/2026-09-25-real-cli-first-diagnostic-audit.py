import shlex
def shell_inner(command: str) -> str | None:
    try:
        words = shlex.split(command)
    except ValueError:
        return None
    if len(words) == 3 and words[:2] == ["/bin/zsh", "-lc"]:
        return words[2]
    return command


def audit_trace(text, expected, expected_exit=None, *, allow_open_turn=False):
    """Audit the first diagnostic; allow an open turn only after verified cancellation.

    The caller must supply allow_open_turn only when its process supervisor
    independently observed a timeout and verified process-tree cleanup.
    """
    import json
    commands = []
    malformed = 0
    actions_before = []
    first_id = None
    first_completed = False
    first_exit_code = None
    command_mismatch = False
    thread_started = turn_started = turn_completed = turn_failed = False
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            malformed += 1
            continue
        if not isinstance(event, dict):
            malformed += 1
            continue
        kind = event.get('type')
        if turn_completed or turn_failed:
            malformed += 1
            continue
        if kind == 'thread.started':
            if thread_started or turn_started:
                malformed += 1
            thread_started = True
            continue
        if kind == 'turn.started':
            if not thread_started or turn_started:
                malformed += 1
            turn_started = True
            continue
        if kind == 'turn.completed':
            if not turn_started or not first_completed:
                malformed += 1
            turn_completed = True
            continue
        if kind == 'turn.failed' and allow_open_turn and first_completed:
            turn_failed = True
            continue
        if kind in ('turn.failed', 'error'):
            malformed += 1
            continue
        if kind not in ('item.started', 'item.updated', 'item.completed'):
            malformed += 1
            continue
        item = event.get('item')
        if not isinstance(item, dict) or not isinstance(item.get('type'), str):
            malformed += 1
            continue
        item_type = item['type']
        if item_type in ('reasoning', 'agent_message'):
            continue
        if item_type == 'error':
            malformed += 1
            continue
        if not turn_started:
            malformed += 1
            continue
        if item_type != 'command_execution':
            if not first_completed:
                actions_before.append(item_type)
            continue
        if kind == 'item.started':
            command = item.get('command')
            if not isinstance(command, str):
                malformed += 1
                continue
            if commands and not first_completed:
                actions_before.append('overlapping_command_execution')
            commands.append(shell_inner(command))
            if len(commands) == 1:
                first_id = item.get('id')
        elif not commands:
            actions_before.append('command_execution_without_start')
        elif item.get('id') == first_id and kind == 'item.completed':
            if first_completed:
                malformed += 1
                continue
            first_completed = True
            first_exit_code = item.get('exit_code')
            # Codex marks a command with nonzero exit as failed even when the
            # tool action completed and the turn continued normally.
            if (type(first_exit_code) is not int or
                    item.get('status') != ('completed' if first_exit_code == 0 else 'failed')):
                malformed += 1
            if shell_inner(item.get('command', '')) != commands[0]:
                command_mismatch = True
        elif not first_completed and item.get('id') != first_id:
            actions_before.append('other_command_event')
    complete = (thread_started and turn_started and
                (turn_completed or allow_open_turn and first_completed) and
                first_completed and isinstance(first_id, str) and bool(first_id))
    exit_mismatch = expected_exit is not None and first_exit_code != expected_exit
    exact = (complete and bool(commands) and commands[0] == expected and
             not actions_before and not malformed and not command_mismatch and not exit_mismatch)
    return {'first_command_exact': exact,
            'commands_observed': len(commands), 'other_prior_actions': actions_before,
            'malformed_lines': malformed,
            'first_command_completed': first_completed,
            'first_exit_code': first_exit_code,
            'trace_complete': turn_completed and not malformed,
            'status': 'unknown' if malformed or not complete else ('violated' if actions_before or command_mismatch or exit_mismatch or commands[0] != expected else 'observed')}
