import sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from KimsufiStockCommander import Commander, CommandResult, durable_write_text


def make_commander():
    td = tempfile.TemporaryDirectory()
    root = Path(td.name)
    (root / 'ui').mkdir()
    c = Commander(root, demo=True)
    c.settings['autoOrderArmed'] = True
    c.settings['orderDefaults']['catalogMonthlyPriceCap'] = 0.0
    c.settings['orderDefaults']['autoPay'] = True
    c.settings['budgetPolicy'].update({
        'enabled': True,
        'totalLimit': 1000.0,
        'baselineReserved': 0.0,
        'perOrderReserveCap': 1000.0,
        'bufferPercent': 0.0,
        'bufferFixed': 0.0,
        'requireFiniteForAutoPay': True,
    })
    tid = c._target_key('plan-v1', 'ram-x', 'disk-x')
    c.targets[tid] = {
        'id': tid, 'planCode': 'plan-v1', 'name': 'Test', 'memory': 'ram-x', 'storage': 'disk-x',
        'watch': True, 'autoOrder': True, 'desiredQty': 1, 'completedQty': 0,
        'orderPriority': 100, 'review': False, 'reviewReason': '',
        'orderMemoryIncluded': True, 'orderStorageIncluded': True,
        'orderMemoryOption': '', 'orderStorageOption': '',
        'inheritPrice': True, 'priceDuration': 'P1Y', 'priceMode': 'upfront12', 'autoPay': True,
    }
    c.catalog = [{'id': tid, 'planCode': 'plan-v1', 'memory': 'ram-x', 'storage': 'disk-x'}]
    c.catalog_row_orderability = lambda row: {'status': 'orderable'}
    c.auth_ready = lambda: (True, [])
    c.preflight_price_config = lambda target_id: (True, 'P1Y', 'upfront12')
    c.refresh_checkout_estimate = lambda *args, **kwargs: {}
    c.preflight_budget = lambda target_id: (True, {'baseAmount': 100.0, 'reservedAmount': 100.0, 'currency': 'EUR'})
    return td, c, tid


def test_success_disables_target_and_persists_receipt():
    td, c, tid = make_commander()
    calls = {'n': 0}
    def ok_cli(args, timeout=90):
        calls['n'] += 1
        return CommandResult(0, '> order completed: https://example.invalid/order\n', args)
    c.run_cli = ok_cli
    c.maybe_order(tid, 'gra')
    assert calls['n'] == 1
    assert c.targets[tid]['completedQty'] == 1
    assert c.targets[tid]['autoOrder'] is False
    assert not c._active_order_path(tid).exists()
    assert len(c._receipt_glob(tid)) == 1
    # A second poll cannot buy again.
    c.maybe_order(tid, 'gra')
    assert calls['n'] == 1
    td.cleanup()


def test_persistence_failure_after_confirmed_checkout_never_retries():
    td, c, tid = make_commander()
    calls = {'n': 0}
    def ok_cli(args, timeout=90):
        calls['n'] += 1
        return CommandResult(0, '> order completed: https://example.invalid/order\n', args)
    c.run_cli = ok_cli
    def fail_ledger(*args, **kwargs):
        raise OSError('simulated disk full after checkout success')
    c._append_budget_entry = fail_ledger
    try:
        c.maybe_order(tid, 'gra')
    except OSError:
        pass
    assert calls['n'] == 1
    assert c._active_order_path(tid).exists(), 'ambiguous active marker must survive'
    assert c._review_lock_path(tid).exists(), 'must enter persistent REVIEW'
    assert c.targets[tid]['review'] is True
    assert c.targets[tid]['autoOrder'] is False
    assert c.settings['autoOrderArmed'] is False
    # Even if a caller invokes maybe_order again in the same live process, no checkout.
    c.maybe_order(tid, 'gra')
    assert calls['n'] == 1
    td.cleanup()


def test_preexisting_active_marker_is_hard_stop():
    td, c, tid = make_commander()
    durable_write_text(c._active_order_path(tid), 'ACTIVE ORDER\n')
    calls = {'n': 0}
    def should_not_run(args, timeout=90):
        calls['n'] += 1
        return CommandResult(0, '> order completed: impossible\n', args)
    c.run_cli = should_not_run
    c.maybe_order(tid, 'gra')
    assert calls['n'] == 0
    assert c.targets[tid]['review'] is True
    assert c.targets[tid]['autoOrder'] is False
    assert c.settings['autoOrderArmed'] is False
    assert c._review_lock_path(tid).exists()
    td.cleanup()


def test_autopay_requires_finite_budget_by_default():
    td, c, tid = make_commander()
    c.settings['budgetPolicy']['enabled'] = False
    c.settings['budgetPolicy']['totalLimit'] = 0.0
    calls = {'n': 0}
    def should_not_run(args, timeout=90):
        calls['n'] += 1
        return CommandResult(0, '> order completed: impossible\n', args)
    c.run_cli = should_not_run
    c.maybe_order(tid, 'gra')
    assert calls['n'] == 0
    assert c.targets[tid]['lastOrderResult'] == 'finite_budget_required'
    td.cleanup()


def test_readd_same_target_restores_receipt_quota():
    td, c, tid = make_commander()
    # Simulate an already-confirmed purchase whose target was later removed.
    receipt = c.data / 'receipts' / f"{c._target_hash(tid)}-success-001.txt"
    durable_write_text(receipt, 'SUCCESS\nTarget='+tid+'\n')
    c.targets.pop(tid, None)
    t = c.upsert_target({
        'planCode':'plan-v1','name':'Test','memory':'ram-x','storage':'disk-x',
        'watch':True,'autoOrder':True,'desiredQty':1,'autoPay':True
    })
    assert t['completedQty'] >= 1
    assert t['autoOrder'] is False
    td.cleanup()


if __name__ == '__main__':
    test_success_disables_target_and_persists_receipt()
    test_persistence_failure_after_confirmed_checkout_never_retries()
    test_preexisting_active_marker_is_hard_stop()
    test_autopay_requires_finite_budget_by_default()
    test_readd_same_target_restores_receipt_quota()
    print('v0.18 order safety tests: OK')