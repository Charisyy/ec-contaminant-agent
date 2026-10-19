import json
import urllib.error
import urllib.parse
import urllib.request

import pytest

from ec_agent.db import connect_readonly, search
from test_delivery import local_server


def categories(con):
    return [row[0] for row in con.execute('SELECT DISTINCT category FROM entities ORDER BY category LIMIT 2')]


def test_category_union_duplicates_and_legacy_value():
    with connect_readonly() as con:
        first, second = categories(con)
        a, b = search(con, category=first), search(con, category=second)
        both = search(con, category=[first, second, first, ''])
        assert a['total'] > 0 and b['total'] > 0
        assert both['total'] == a['total'] + b['total']
        assert {row['category'] for row in both['rows']} <= {first, second}
        assert search(con, category=[])['total'] == search(con)['total'] == 1216
        assert search(con, category=["x') OR 1=1 --"])['total'] == 0


def test_filter_intersection_pagination_and_stable_ids():
    with connect_readonly() as con:
        chosen = categories(con)
        expected = [row[0] for row in con.execute('''SELECT id FROM entities
            WHERE category IN (?,?) AND measured=1 AND experiment_candidate IN (0,1)
            AND selected_structure_id IS NOT NULL ORDER BY id''', chosen)]
        assert expected
        actual = []
        for page in range(1, (len(expected) + 4) // 5 + 1):
            result = search(con, category=chosen, measured=['1'], experiment=['0','1'],
                structure=['yes'], page=page, limit=5)
            assert result['total'] == len(expected)
            actual.extend(row['id'] for row in result['rows'])
        assert actual == expected and len(actual) == len(set(actual))


@pytest.mark.parametrize('field,values', [('experiment',['1','0']),('measured',['0','1']),('structure',['yes','no'])])
def test_both_binary_states_cover_full_catalog(field, values):
    with connect_readonly() as con:
        assert search(con, **{field:values})['total'] == 1216


def test_empty_union_keeps_cluster_and_historical_group_constraints():
    with connect_readonly() as con:
        member = con.execute('SELECT run_id,cluster_id FROM cluster_members LIMIT 1').fetchone()
        single = search(con, run_id=member['run_id'], cluster=member['cluster_id'])
        both = search(con, run_id=member['run_id'], cluster=member['cluster_id'], structure=['yes','no'])
        assert single['total'] > 0 and both == single
        group = con.execute("SELECT j.key FROM structures s,json_each(s.features_json,'$.functional_groups') j LIMIT 1").fetchone()[0]
        assert search(con, group=group, measured=['0','1']) == search(con, group=group)


def test_http_repeated_filters_are_unioned_and_combined(local_server):
    with connect_readonly() as con:
        chosen = categories(con)
        expected = con.execute('SELECT count(*) FROM entities WHERE category IN (?,?) AND measured=1', chosen).fetchone()[0]
    query = urllib.parse.urlencode([('category',value) for value in chosen] + [('measured','1'),('structure','yes'),('structure','no')])
    with urllib.request.urlopen(local_server+'/api/entities?'+query) as response:
        result = json.load(response)
    assert result['total'] == expected > 0
    assert all(row['category'] in chosen and row['measured']==1 for row in result['rows'])


@pytest.mark.parametrize('query', ['structure=yes&structure=invalid','measured=1&measured=2','experiment=invalid'])
def test_http_invalid_filter_values_return_400(local_server, query):
    with pytest.raises(urllib.error.HTTPError) as error:
        urllib.request.urlopen(local_server+'/api/entities?'+query)
    assert error.value.code == 400
