import requests
import json
import os
from datetime import datetime
from typing import List, Dict, Optional

# 导入Function Calling网络搜索工具
try:
    from web_search_tools import FunctionCallHandler
    WEB_SEARCH_AVAILABLE = True
except ImportError:
    WEB_SEARCH_AVAILABLE = False
    FunctionCallHandler = None

OLLAMA_URL = "http://192.168.1.101:11434/api/chat"
OLLAMA_MODEL = "qwen3:8b"  # 

class LLMChat:
    def __init__(self, history_file="chat_history.json"):
        self.history_file = history_file
        self.conversation_history = []
        self.enable_web_search = False
        # 可配置的设置
        self.server_url = OLLAMA_URL
        self.model_name = OLLAMA_MODEL
        self.temperature = 0.7
        self.max_tokens = 2000
        self.auto_save = True
        self.history_limit = 100
        # 初始化Function Calling处理器
        self.function_handler = FunctionCallHandler() if WEB_SEARCH_AVAILABLE else None
        if self.function_handler:
            self.function_handler.load_api_config()
        self.load_history()
        self.load_settings()
    
    def set_web_search(self, enabled: bool):
        """开启或关闭联网搜索功能"""
        self.enable_web_search = enabled
        # 保存联网搜索状态到设置
        self.save_settings()
    
    def load_history(self):
        """加载历史对话"""
        try:
            if os.path.exists(self.history_file):
                with open(self.history_file, 'r', encoding='utf-8') as f:
                    self.conversation_history = json.load(f)
        except Exception as e:
            print(f"加载历史对话失败: {e}")
            self.conversation_history = []
    
    def save_history(self):
        """保存历史对话"""
        try:
            with open(self.history_file, 'w', encoding='utf-8') as f:
                json.dump(self.conversation_history, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存历史对话失败: {e}")
    
    def clear_history(self):
        """清除历史对话"""
        self.conversation_history = []
        self.save_history()
    
    def load_settings(self):
        """加载设置"""
        try:
            settings_file = "chat_settings.json"
            if os.path.exists(settings_file):
                with open(settings_file, 'r', encoding='utf-8') as f:
                    settings = json.load(f)
                    self.server_url = settings.get('server_url', OLLAMA_URL)
                    self.model_name = settings.get('model_name', OLLAMA_MODEL)
                    self.temperature = settings.get('temperature', 0.7)
                    self.max_tokens = settings.get('max_tokens', 2000)
                    self.auto_save = settings.get('auto_save', True)
                    self.history_limit = settings.get('history_limit', 100)
                    self.enable_web_search = settings.get('enable_web_search', False)
        except Exception as e:
            print(f"加载设置失败: {e}")
    
    def save_settings(self):
        """保存设置"""
        try:
            settings = {
                'server_url': self.server_url,
                'model_name': self.model_name,
                'temperature': self.temperature,
                'max_tokens': self.max_tokens,
                'auto_save': self.auto_save,
                'history_limit': self.history_limit,
                'enable_web_search': self.enable_web_search
            }
            with open("chat_settings.json", 'w', encoding='utf-8') as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"保存设置失败: {e}")
    
    def update_settings(self, **kwargs):
        """更新设置"""
        for key, value in kwargs.items():
            if hasattr(self, key):
                setattr(self, key, value)
        self.save_settings()
    
    def add_system_context(self, context: str):
        """添加系统上下文信息"""
        system_message = {
            "role": "system",
            "content": f"你是一个专业的功放测试分析助手。当前测试环境信息：{context}"
        }
        return system_message
    
    def chat(self, user_message: str, system_context: Optional[str] = None) -> str:
        """
        发送消息并获取回复，支持Function Calling
        """
        try:
            # 构建消息列表
            messages = []
            
            # 添加系统上下文
            expert_prompt = (
                "你现在是一位拥有15年经验的资深微波射频测试专家与自动化测试工程师。你的核心任务是协助实验室测试人员解决射频功率放大器（PA）在大信号测试过程中的理论疑问、软硬件故障排查以及仪器程控（SCPI）问题。\n\n"
                "在回答用户问题时，请严格遵循以下约束原则：\n\n"
                "专业严谨：必须基于射频微波理论（如阻抗失配、非线性失真、自热效应、偏置网络等）进行原理分析。\n"
                "工程导向：不要长篇大论的空洞理论，回答必须直接提供具有可操作性的‘排查步骤’或‘测试建议’。\n"
                "结构清晰：请务必采用‘分条列举（1, 2, 3...）’的方式输出可能的原因和解决对策，条理分明。\n"
                "代码规范：如果用户的提问涉及程控仪器控制，请直接输出标准且兼容性强的 SCPI 伪代码或指令示例。\n"
                "安全第一：在涉及大功率、高电压的异常排障时，必须在回答的开头优先提醒切断电源或降低射频激励以保护样片。\n\n"
            )
            system_msg_content = expert_prompt
            if system_context:
                system_msg_content += f"当前测试环境和数据上下文信息如下：\n{system_context}"
            
            if self.enable_web_search and self.function_handler:
                current_date = datetime.now().strftime("%Y年%m月%d日")
                system_msg_content += f"\n\n当前日期：{current_date}。你现在具备联网搜索功能。当用户询问最新信息、实时数据、当前产品价格、新品发布等需要最新信息的问题时，你可以使用search_web函数来获取实时信息。"
            elif self.enable_web_search:
                current_date = datetime.now().strftime("%Y年%m月%d日")
                system_msg_content += f"\n\n当前日期：{current_date}。联网搜索功能已启用但搜索工具不可用，请告知用户你无法进行实时搜索，建议访问官方网站获取最新信息。"
                
            if system_msg_content:
                messages.append({"role": "system", "content": system_msg_content})
            
            # 添加历史对话（最近10轮）
            recent_history = self.conversation_history[-20:] if len(self.conversation_history) > 20 else self.conversation_history
            messages.extend(recent_history)
            
            # 添加当前用户消息
            user_msg = {"role": "user", "content": user_message}
            messages.append(user_msg)
            
            # 联网搜索预处理
            search_results = ""
            if self.enable_web_search and self.function_handler:
                # 检测是否需要网络搜索的关键词
                search_keywords = ['最新', '新款', '型号', '价格', '发布', '什么时候', '多少钱', '哪些', '现在', '目前', '当前']
                if any(keyword in user_message for keyword in search_keywords):
                    try:
                        # 执行搜索
                        function_result = self.function_handler.search_web(user_message, 5)
                        if function_result.get('success'):
                            search_results = f"网络搜索结果：\n"
                            for item in function_result['results'][:3]:  # 只取前3个结果
                                search_results += f"- {item['标题']}\n  {item['摘要']}\n  来源：{item['来源']}\n\n"
                        elif 'error' in function_result:
                            search_results = f"搜索提示：{function_result['error']}\n"
                    except Exception as e:
                        search_results = f"搜索遇到问题：{str(e)}\n"
            
            # 如果有搜索结果，添加到系统消息中
            if search_results:
                if system_msg_content:
                    messages[0]["content"] += f"\n\n{search_results}请基于以上搜索结果回答用户问题。"
                else:
                    messages.insert(0, {"role": "system", "content": f"{search_results}请基于以上搜索结果回答用户问题。"})
            
            # 构建请求数据（不包含tools参数，因为Ollama不支持）
            request_data = {
                "model": self.model_name,
                "messages": messages,
                "stream": False,
                "options": {
                    "temperature": self.temperature,
                    "top_p": 0.9,
                    "max_tokens": self.max_tokens
                }
            }
            
            # 发送请求
            response = requests.post(self.server_url, json=request_data, timeout=60)
            response.raise_for_status()
            data = response.json()
            
            assistant_reply = data['message']['content']
            
            # 保存到历史对话
            self.conversation_history.append(user_msg)
            self.conversation_history.append({
                "role": "assistant", 
                "content": assistant_reply,
                "timestamp": datetime.now().isoformat()
            })
            
            # 自动保存历史
            self.save_history()
            
            return assistant_reply
            
        except requests.exceptions.Timeout:
            return "请求超时，请稍后重试。"
        except requests.exceptions.ConnectionError:
            return "无法连接到大模型服务，请检查网络连接。"
        except Exception as e:
            return f"发生错误: {str(e)}"

def chat(messages):
    """
    兼容性函数
    messages: List[Dict], e.g.
    [{"role": "user", "content": "你好"}]
    """
    resp = requests.post(OLLAMA_URL, json={
        "model": OLLAMA_MODEL,
        "messages": messages,
        "stream": False
    })
    resp.raise_for_status()
    data = resp.json()
    return data['message']['content']