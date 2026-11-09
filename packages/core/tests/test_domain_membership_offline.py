"""Predicate-dimensional regression; synthetic semantic oracles, NO real-model accuracy claim."""
import test_offline
import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock,patch
import place_write as w
from ddd_criterion import DOMAIN_SAME_QUESTION,DOMAIN_QUESTION


class DomainMembership(unittest.TestCase):
 def fixture(self,member=.91,subject=False):
  unit={'content':'배송 보관함은 배송품을 보관한다.','target_expression':'배송 보관함','source_ranges':[[1,1]]}
  catalog=[{'name':'배송함' if subject else '물류','description':'배송품을 보관하는 배송함' if subject else '배송·창고·출고 업무 영역','aliases':[]},
           {'name':'고객' if subject else '인사','description':'고객 대상' if subject else '채용·근태 업무 영역','aliases':[]}]
  calls=[]
  def ranked(*args,**kw):return [dict(v,key='c'+str(i),rank=i+1,similarity=.2) for i,v in enumerate(catalog)]
  def choose(kind,packet,options):return {'c0':.35,'c1':.1,'none':.55}
  def ask(packet,labels,question):
   calls.append((copy.deepcopy(packet),question))
   if question==DOMAIN_SAME_QUESTION:return [.02]  # area != the unit's owning entity
   if question==w.SAME_QUESTION:return [.91]       # same subject with a different surface name
   return [member]                              # explicit area membership predicate
  ports=SimpleNamespace(choose=choose,ask=ask,worker=Mock(side_effect=AssertionError('naming forbidden')),encode=Mock(side_effect=AssertionError('embedding forbidden')))
  return unit,catalog,ports,calls,ranked
 def test_entity_belongs_to_area_is_not_area_identity(self):
  unit,catalog,ports,calls,ranked=self.fixture();trace={'searches':[]}
  with patch.object(w,'ranked',side_effect=ranked):chosen,_=w.search_choice('domain',unit,None,catalog,ports,5,trace)
  self.assertIsNotNone(chosen,'domain fallback asked whether a business area and an entity were the SAME AREA, not membership')
  self.assertEqual(chosen['name'],'물류')
  self.assertNotEqual(calls[-1][1],DOMAIN_SAME_QUESTION)
  self.assertNotIn('proposed',calls[-1][0])
 def test_membership_does_not_grant_entity_alias_to_domain(self):
  unit,catalog,ports,calls,ranked=self.fixture()
  with patch.object(w,'ranked',side_effect=ranked):trace=w.place_identity('domain',unit,None,catalog,ports,5,document=unit['content'])
  self.assertFalse(trace['new']);self.assertEqual(trace['chosen']['name'],'물류');self.assertEqual(trace['chosen']['aliases'],[])
  self.assertEqual(trace['same_meaning_checks'],[]);ports.worker.assert_not_called();ports.encode.assert_not_called()
  self.assertEqual(calls[-1][0]['source'],unit['content']);self.assertEqual(unit['target_expression'],'배송 보관함')
  self.assertIn('ambiguous_domain_membership',trace);self.assertNotIn('ambiguous_same_meaning',trace)
 def test_related_or_uncertain_membership_does_not_select(self):
  for value in (.01,.49):
   with self.subTest(value=value):
    unit,catalog,ports,calls,ranked=self.fixture(member=value);trace={'searches':[]}
    with patch.object(w,'ranked',side_effect=ranked):chosen,_=w.search_choice('domain',unit,None,catalog,ports,5,trace)
    self.assertIsNone(chosen);self.assertFalse(trace['ambiguous_domain_membership']['member'])
 def test_existing_binary_fallback_boundary_is_preserved(self):
  unit,catalog,ports,calls,ranked=self.fixture(member=.5);trace={'searches':[]}
  with patch.object(w,'ranked',side_effect=ranked):chosen,_=w.search_choice('domain',unit,None,catalog,ports,5,trace)
  self.assertEqual(chosen['name'],'물류')
 def test_subject_fallback_still_asks_same_entity(self):
  unit,catalog,ports,calls,ranked=self.fixture(subject=True);trace={'searches':[]}
  with patch.object(w,'ranked',side_effect=ranked):chosen,_=w.search_choice('subject',unit,{'name':'물류'},catalog,ports,5,trace)
  self.assertEqual(chosen['name'],'배송함');self.assertEqual(calls[-1][1],w.SAME_QUESTION)
  self.assertIn('proposed',calls[-1][0]);self.assertNotIn('ambiguous_domain_membership',trace)
 def test_membership_no_does_not_lower_new_domain_none_point_seven(self):
  unit={'content':'배송함은 배송품을 보관한다.','target_expression':'배송함','source_ranges':[[1,1]]}
  catalog=[{'name':'인사','description':'채용·근태 업무 영역','aliases':[]},{'name':'재무','description':'회계 업무 영역','aliases':[]}]
  def ranked(*args,**kw):return [dict(v,key='c'+str(i),rank=i+1,similarity=.1) for i,v in enumerate(catalog)]
  def choose(kind,packet,options):
   result={key:0. for key in options};result[next(key for key in options if key!='none')]=.41;result['none']=.59;return result
  def ask(packet,labels,question):return [.9 if question==DOMAIN_QUESTION else .1]
  ports=SimpleNamespace(choose=choose,ask=ask,worker=lambda *args:{'source_concept':'배송함','name':'물류','description':'배송품 운송·보관 업무 영역'},encode=Mock(side_effect=AssertionError('embedding forbidden')))
  with patch.object(w,'ranked',side_effect=ranked):
   with self.assertRaises(w.PlacementHeld) as held:w.place_identity('domain',unit,None,catalog,ports,5,document=unit['content'])
  self.assertEqual(str(held.exception),'new domain requires explicit Jev none')
  self.assertEqual(held.exception.trace['domain_creation_choice']['none'],.59);ports.encode.assert_not_called()


if __name__=='__main__':unittest.main()
