import os
import random
import string
from copy import deepcopy
from pathlib import Path

from django.test import TestCase

from anon_app.models import Node, Edge, Chain
from anon_app.tasks.cmd import SSHCopyIdCmd, AutoSSHCmd, KillProcCmd, ClearBuildCmd, ScpCmd, SSGetFreePortCmd, \
 SSHKeyGenCmd, AnsiblePlaybookCmd
from anon_app.tests.datasource import get_new_node_data, get_new_chain_data
from soi_app.settings import MEDIA_ROOT, DATA_PREFIX


class BaseCmdTestMixin:
 # noinspection PyUnresolvedReferences
 def test_serialize(self):
  serialized = self.cmd.serialize()
  self.assertTrue(self.cmd.__class__.deserialize(*serialized) == self.cmd)

 # noinspection PyUnresolvedReferences
 def test_copy(self):
  cmd2 = deepcopy(self.cmd)
  self.assertTrue(cmd2 == self.cmd)


class CmdChainTest(TestCase):
 id_rsa_path: str
 id_rsa_pub_path: str

 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(CmdChainTest, cls).setUpClass()

  cls.id_rsa_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}')
  cls.id_rsa_pub_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}.pub')

  open(cls.id_rsa_path, 'w').close()
  open(cls.id_rsa_pub_path, 'w').close()

  files = {
   'id_rsa': cls.id_rsa_path,
   'id_rsa_pub': cls.id_rsa_pub_path,
  }

  _obj, _ = get_new_chain_data(node_files=files)
  _edges = _obj.pop('edges')
  cls.chain = Chain.objects.create(**_obj)

  for _edge in _edges:
   Edge.objects.create(**_edge, chain=cls.chain)

  cls.edges = cls.chain.sorted_edges

 def test_create(self):
  cmd_chain = SSHCopyIdCmd(self.edges[0].out_node, is_forwarded=True) \
     | AutoSSHCmd(self.edges[0], is_forwarded=True)

  for edge in self.edges[1:]:
   cmd_chain |= SSHCopyIdCmd(edge.out_node, is_forwarded=False) | AutoSSHCmd(edge, is_forwarded=False)
  cmd_chain = cmd_chain | SSHCopyIdCmd(self.edges[-1].in_node)

  self.assertTrue([
   cmd.__class__ == (SSHCopyIdCmd if i % 2 == 0 else AutoSSHCmd)
   for i, cmd in enumerate(cmd_chain.todo)
  ])

 @classmethod
 def tearDownClass(cls):
  super(CmdChainTest, cls).tearDownClass()
  os.remove(cls.id_rsa_path)
  os.remove(cls.id_rsa_pub_path)


class AutoSSHCmdTest(TestCase, BaseCmdTestMixin):
 id_rsa_path: str
 id_rsa_pub_path: str

 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(AutoSSHCmdTest, cls).setUpClass()

  cls.id_rsa_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}')
  cls.id_rsa_pub_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}.pub')

  open(cls.id_rsa_path, 'w').close()
  open(cls.id_rsa_pub_path, 'w').close()

  files = {
   'id_rsa': cls.id_rsa_path,
   'id_rsa_pub': cls.id_rsa_pub_path,
  }

  obj_, _ = get_new_chain_data(node_files=files)
  _index, _edge = random.choice(list(enumerate(obj_.pop('edges'))))
  _chain = Chain.objects.create(**obj_)

  cls.obj = Edge.objects.create(**_edge, chain=_chain)
  cls.cmd = AutoSSHCmd(cls.obj, is_forwarded=_index == 0)

 @classmethod
 def tearDownClass(cls):
  super(AutoSSHCmdTest, cls).tearDownClass()
  os.remove(cls.id_rsa_path)
  os.remove(cls.id_rsa_pub_path)


class SSHCopyIdCmdTest(TestCase, BaseCmdTestMixin):
 id_rsa_path: str
 id_rsa_pub_path: str

 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(SSHCopyIdCmdTest, cls).setUpClass()
  cls.id_rsa_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}')
  cls.id_rsa_pub_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}.pub')

  open(cls.id_rsa_path, 'w').close()
  open(cls.id_rsa_pub_path, 'w').close()

  files = {
   'id_rsa': cls.id_rsa_path,
   'id_rsa_pub': cls.id_rsa_pub_path,
  }

  cls.obj = Node.objects.create(**get_new_node_data(node_files=files)[0])
  cls.cmd = SSHCopyIdCmd(cls.obj)

 @classmethod
 def tearDownClass(cls):
  super(SSHCopyIdCmdTest, cls).tearDownClass()
  os.remove(cls.id_rsa_path)
  os.remove(cls.id_rsa_pub_path)


class KillProcCmdTest(TestCase, BaseCmdTestMixin):
 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(KillProcCmdTest, cls).setUpClass()

  cls.obj = ''.join(random.choices(string.ascii_letters + string.digits, k=16))
  cls.cmd = KillProcCmd(cls.obj)


class ClearBuildCmdTest(TestCase, BaseCmdTestMixin):
 id_rsa_path: str
 id_rsa_pub_path: str

 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(ClearBuildCmdTest, cls).setUpClass()

  cls.id_rsa_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}')
  cls.id_rsa_pub_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}.pub')

  open(cls.id_rsa_path, 'w').close()
  open(cls.id_rsa_pub_path, 'w').close()

  files = {
   'id_rsa': cls.id_rsa_path,
   'id_rsa_pub': cls.id_rsa_pub_path,
  }

  obj_, _ = get_new_chain_data(node_files=files)
  edges = obj_.pop('edges')
  chain = Chain.objects.create(**obj_)
  for edge in edges:
   Edge.objects.create(**edge, chain=chain)

  cls.obj = chain
  cls.cmd = ClearBuildCmd(cls.obj)

 @classmethod
 def tearDownClass(cls):
  super(ClearBuildCmdTest, cls).tearDownClass()
  os.remove(cls.id_rsa_path)
  os.remove(cls.id_rsa_pub_path)


class ScpCmdTest(TestCase, BaseCmdTestMixin):
 id_rsa_path: str
 id_rsa_pub_path: str

 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(ScpCmdTest, cls).setUpClass()
  cls.id_rsa_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}')
  cls.id_rsa_pub_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}.pub')

  open(cls.id_rsa_path, 'w').close()
  open(cls.id_rsa_pub_path, 'w').close()

  files = {
   'id_rsa': cls.id_rsa_path,
   'id_rsa_pub': cls.id_rsa_pub_path,
  }

  cls.obj = Node.objects.create(**get_new_node_data(node_files=files)[0])
  cls.cmd = ScpCmd(
   local_path='/path/to/local', remote_path=Path('/path/to/remote'),
   node=cls.obj, is_forwarded=True, send=True
  )

 def test_space_problem(self):
  cmd = ScpCmd(
   local_path='/path/to space/local', remote_path=Path('/path/to space/remote'),
   node=self.obj, is_forwarded=True, send=True
  )
  sh = cmd.serialize()[0]

  self.assertIn('localhost:"/path/to\ space/remote"', sh)

 @classmethod
 def tearDownClass(cls):
  super(ScpCmdTest, cls).tearDownClass()
  os.remove(cls.id_rsa_path)
  os.remove(cls.id_rsa_pub_path)


class SSGetFreePortCmdTest(TestCase, BaseCmdTestMixin):
 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(SSGetFreePortCmdTest, cls).setUpClass()

  cls.obj = None
  cls.cmd = SSGetFreePortCmd()


class SSHKeyGenTest(TestCase, BaseCmdTestMixin):
 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(SSHKeyGenTest, cls).setUpClass()

  cls.obj = '/path/to/key'
  cls.cmd = SSHKeyGenCmd(cls.obj)


class AnsiblePlaybookCmdTest(TestCase, BaseCmdTestMixin):
 id_rsa_path: str
 id_rsa_pub_path: str
 cmd: AnsiblePlaybookCmd

 # noinspection DuplicatedCode
 @classmethod
 def setUpClass(cls):
  super(AnsiblePlaybookCmdTest, cls).setUpClass()
  cls.id_rsa_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}')
  cls.id_rsa_pub_path = os.path.join(MEDIA_ROOT, f'test_id_rsa_{random.randint(0, 100000)}.pub')

  open(cls.id_rsa_path, 'w').close()
  open(cls.id_rsa_pub_path, 'w').close()

  files = {
   'id_rsa': cls.id_rsa_path,
   'id_rsa_pub': cls.id_rsa_pub_path,
  }

  plb_path = Path(DATA_PREFIX, 'anon_app/ansible-playbooks/ping.yml')

  cls.obj = Node.objects.create(**get_new_node_data(node_files=files)[0])
  cls.cmd = AnsiblePlaybookCmd(node=cls.obj, is_forwarded=True, playbook_path=plb_path)

 @classmethod
 def tearDownClass(cls):
  super(AnsiblePlaybookCmdTest, cls).tearDownClass()
  os.remove(cls.id_rsa_path)
  os.remove(cls.id_rsa_pub_path)
  cls.cmd.kill()