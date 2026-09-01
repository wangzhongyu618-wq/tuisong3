# coding=utf-8
"""pytest 全局配置

- LITELLM_LOCAL_MODEL_COST_MAP=True：阻止 LiteLLM 在 import 时拉取远程
  model cost map（弱网/被墙环境下 SSL 失败重试会阻塞 import 数分钟），
  直接使用包内本地备份。必须在任何 litellm 导入前设置。
"""

import os

os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")
