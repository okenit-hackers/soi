from anon_app.models import Chain, Edge
from anon_app.tests.datasource import get_new_chain_data


def get_new_lmgs_task_data(**init_data):
 task_queue_name = init_data.pop('task_queue_name') \
  if init_data.get('task_queue_name') is not None else None

 chain_data, _ = get_new_chain_data(
  task_queue_name=task_queue_name
 )

 edges = chain_data.pop('edges')
 chain = Chain.objects.create(**chain_data)

 for edge in edges:
  Edge.objects.create(**edge, chain=chain)

 return {
  "action": "create_vk_bot",
  "kwargs": {},
  "chain": chain,
  **init_data
 }