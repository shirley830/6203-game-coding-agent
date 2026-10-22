"""Compatibility with the Controller's existing failed-criteria summary.

Read only verifier-origin, controller-formatted results for frozen criteria.
Never interpret arbitrary runtime output as a verdict about the test itself.
"""
import re

from agent.state import AgentView, FailureRecord, Phase

_PREFIX = ('Verification failed, evidence is missing, or the report is stale'
           '. Failed criteria: ')
_RESULT = re.compile(r'(?:^|; )([^\s;=:]+)=(pass|fail|unknown|skip): ')


def verifier_fault_reason(failure: FailureRecord, view: AgentView) -> str | None:
    if (failure.category != 'verification'
            or failure.operation_key != 'verifier.next_check'
            or failure.phase != Phase.VERIFYING
            or failure.side_effects_unknown
            or view.suite is None
            or not failure.message.startswith(_PREFIX)):
        return None
    criteria = {c.criterion_id: c for c in view.suite.criteria}
    summary = failure.message[len(_PREFIX):]
    matches = list(_RESULT.finditer(summary))
    for index, match in enumerate(matches):
        cid, status = match.groups()
        criterion = criteria.get(cid)
        if criterion is None:
            continue
        end = matches[index + 1].start() if index + 1 < len(matches) else len(summary)
        detail = summary[match.end():end]
        if (cid == 'C-ACCEPTANCE-INCOMPLETE' and criterion.mandatory
                and status == 'fail'):
            return ('Frozen acceptance is incomplete; game edits cannot repair its missing '
                    'requirements. Preserve the game and evidence, correct acceptance preparation '
                    'and start a new run. Do not weaken frozen criteria or claim success.')
        if status == 'unknown' and re.search(r'\btest_script_error\s*:', detail):
            return (f'Verifier marked {cid} UNKNOWN with test_script_error; this is not a '
                    'gameplay verdict. Preserve the game and evidence and correct the test '
                    'before a new run. Do not repair the game to satisfy a broken assertion.')
    return None
