"""Hand-counted service buckets and honest absence of deletion history."""
from datetime import datetime, timedelta, timezone

import pytest

from app.contracts import ModelSettings, SourceSnapshot
from app.domain.churn_chart import renewal_chart
from app.evaluation import calculate_evaluation

AS_OF = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)


def source():
    services = []
    # In-horizon services have high score at threshold 2; one shared budget
    # covers only the first expiry, so the scenario differs from the fact.
    for identifier, days, status in [(1, -1, 'active'), (2, 0, 'active'), (3, 7, 'active'),
                                      (4, 14, 'suspended'), (5, 30, 'prolong_wait'),
                                      (6, 31, 'active'), (7, 1, 'deleted'), (8, 1, 'blocked')]:
        services.append(dict(id=identifier, name='Synthetic service', ownerId=1, paymentTerm='month',
                             autoProlong=False, createdAt='2026-01-01T00:00:00Z',
                             expiresAt=(AS_OF+timedelta(days=days)).isoformat(), status=status, price=1000))
    return SourceSnapshot.model_validate(dict(accounts=[dict(id=1,email='synthetic@example.test',balance=1000,
        withdrawBalance=0,totalReplenished=1000,bonusBalance=0,bonusState='locked')],services=services,transactions=[]))


def evaluation(snapshot=None, horizon=30):
    settings=ModelSettings.model_validate({'horizon_days':horizon,'thresholds':{'medium':1,'high':2}})
    return calculate_evaluation(snapshot or source(),settings,analysis_revision=1,data_revision=2,settings_revision=3,as_of=AS_OF)


def test_counts_and_scenario_are_integer_service_counts_from_one_evaluation():
    current=evaluation(); result=renewal_chart(current)
    assert result['risk_counts']=={'low':1,'medium':0,'high':5,'unknown':0}
    assert result['excluded_current_status_counts']=={'deleted':1,'blocked':1}
    assert result['eligible_outside_horizon']==1
    assert [(b['high_risk_services'],b['scenario_nonrenewals']) for b in result['buckets']]==[(1,0),(2,2),(1,1),(1,1)]
    assert result['history']['available'] is False
    assert result['as_of']==AS_OF.isoformat()
    assert current.data_revision==2 and current.settings_revision==3


@pytest.mark.parametrize('horizon',[1,7,8,14,90])
def test_horizon_boundaries_are_unique_and_scenario_is_always_a_subset(horizon):
    result=renewal_chart(evaluation(horizon=horizon))
    ends=[b['end'] for b in result['buckets'][1:]]
    assert ends==sorted(set(ends))
    assert ends[-1]==(AS_OF+timedelta(days=horizon)).isoformat()
    assert all(0<=b['scenario_nonrenewals']<=b['high_risk_services'] for b in result['buckets'])


def test_empty_snapshot_is_empty_not_an_invented_history():
    result=renewal_chart(evaluation(SourceSnapshot(accounts=[],services=[],transactions=[])))
    assert sum(result['risk_counts'].values())==0
    assert all(b['high_risk_services']==b['scenario_nonrenewals']==0 for b in result['buckets'])
    assert not result['history']['available']


def test_threshold_change_affects_real_service_counts():
    before=evaluation()
    settings=ModelSettings.model_validate({**before.settings.model_dump(mode='json'),'thresholds':{'medium':1,'high':100}})
    after=calculate_evaluation(before.snapshot,settings,analysis_revision=2,data_revision=2,settings_revision=4,as_of=AS_OF)
    assert sum(b['high_risk_services'] for b in renewal_chart(after)['buckets']) < sum(b['high_risk_services'] for b in renewal_chart(before)['buckets'])
