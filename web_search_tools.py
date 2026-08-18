#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网络搜索工具模块
使用 Function Calling 实现真正的联网搜索功能
"""

import requests
import json
import os
import time
from typing import List, Dict, Any, Optional
from urllib.parse import quote


class WebSearchAPI:
    """网络搜索API封装类"""
    
    def __init__(self):
        # Bing Search API 配置
        self.bing_subscription_key = None  # 用户需要配置
        self.bing_endpoint = "https://api.bing.microsoft.com/v7.0/search"
        
        # Google Custom Search API 配置
        self.google_api_key = None  # 用户需要配置
        self.google_cx = None       # 用户需要配置
        self.google_endpoint = "https://www.googleapis.com/customsearch/v1"
        
        # 备用搜索引擎（使用DuckDuckGo Instant Answer API）
        self.duckduckgo_endpoint = "https://api.duckduckgo.com/"
        
    def search_bing(self, query: str, count: int = 5) -> List[Dict[str, Any]]:
        """使用Bing搜索API"""
        if not self.bing_subscription_key:
            raise ValueError("Bing API密钥未配置")
            
        headers = {
            'Ocp-Apim-Subscription-Key': self.bing_subscription_key,
        }
        
        params = {
            'q': query,
            'count': count,
            'responseFilter': 'Webpages',
            'textFormat': 'HTML',
            'freshness': 'Month'  # 获取最近一个月的结果
        }
        
        try:
            response = requests.get(self.bing_endpoint, headers=headers, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            results = []
            if 'webPages' in data and 'value' in data['webPages']:
                for item in data['webPages']['value']:
                    results.append({
                        'title': item.get('name', ''),
                        'url': item.get('url', ''),
                        'snippet': item.get('snippet', ''),
                        'date_published': item.get('datePublished', ''),
                        'source': 'Bing'
                    })
            
            return results
        except Exception as e:
            raise Exception(f"Bing搜索失败: {str(e)}")
    
    def search_google(self, query: str, count: int = 5) -> List[Dict[str, Any]]:
        """使用Google自定义搜索API"""
        if not self.google_api_key or not self.google_cx:
            raise ValueError("Google API密钥或搜索引擎ID未配置")
            
        params = {
            'key': self.google_api_key,
            'cx': self.google_cx,
            'q': query,
            'num': count,
            'dateRestrict': 'm1'  # 最近一个月
        }
        
        try:
            response = requests.get(self.google_endpoint, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            results = []
            if 'items' in data:
                for item in data['items']:
                    results.append({
                        'title': item.get('title', ''),
                        'url': item.get('link', ''),
                        'snippet': item.get('snippet', ''),
                        'date_published': '',
                        'source': 'Google'
                    })
            
            return results
        except Exception as e:
            raise Exception(f"Google搜索失败: {str(e)}")
    
    def search_duckduckgo(self, query: str) -> List[Dict[str, Any]]:
        """使用DuckDuckGo即时回答API（免费但功能有限）"""
        params = {
            'q': query,
            'format': 'json',
            'no_html': '1',
            'skip_disambig': '1'
        }
        
        try:
            response = requests.get(self.duckduckgo_endpoint, params=params, timeout=10)
            response.raise_for_status()
            data = response.json()
            
            results = []
            
            # 添加即时回答
            if data.get('Abstract'):
                results.append({
                    'title': data.get('AbstractText', '即时回答'),
                    'url': data.get('AbstractURL', ''),
                    'snippet': data.get('Abstract', ''),
                    'date_published': '',
                    'source': 'DuckDuckGo'
                })
            
            # 添加相关主题
            if data.get('RelatedTopics'):
                for topic in data['RelatedTopics'][:3]:  # 只取前3个
                    if isinstance(topic, dict) and topic.get('Text'):
                        results.append({
                            'title': topic.get('Text', '')[:100] + '...',
                            'url': topic.get('FirstURL', ''),
                            'snippet': topic.get('Text', ''),
                            'date_published': '',
                            'source': 'DuckDuckGo'
                        })
            
            return results
        except Exception as e:
            raise Exception(f"DuckDuckGo搜索失败: {str(e)}")
    
    def search(self, query: str, count: int = 5) -> List[Dict[str, Any]]:
        """智能搜索：优先使用配置的API，失败时使用备用方案"""
        results = []
        
        # 优先使用Bing
        if self.bing_subscription_key:
            try:
                results = self.search_bing(query, count)
                if results:
                    return results
            except Exception as e:
                print(f"Bing搜索失败，尝试其他搜索引擎: {e}")
        
        # 其次使用Google
        if self.google_api_key and self.google_cx:
            try:
                results = self.search_google(query, count)
                if results:
                    return results
            except Exception as e:
                print(f"Google搜索失败，尝试其他搜索引擎: {e}")
        
        # 最后使用DuckDuckGo（免费但功能有限）
        try:
            results = self.search_duckduckgo(query)
            if results:
                return results
        except Exception as e:
            print(f"DuckDuckGo搜索失败: {e}")
        
        # 如果所有搜索都失败，返回空结果
        return []


class FunctionCallHandler:
    """Function Calling处理器"""
    
    def __init__(self):
        self.web_search_api = WebSearchAPI()
        
    def get_available_functions(self) -> List[Dict[str, Any]]:
        """获取可用的函数定义"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "search_web",
                    "description": "在互联网上搜索最新信息。当用户询问最新产品、价格、新闻或需要实时数据时使用此功能。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "搜索查询关键词，例如：'小米15最新价格'、'iPhone 15 Pro发布时间'"
                            },
                            "count": {
                                "type": "integer",
                                "description": "返回的搜索结果数量，默认为5",
                                "default": 5,
                                "minimum": 1,
                                "maximum": 10
                            }
                        },
                        "required": ["query"]
                    }
                }
            }
        ]
    
    def execute_function(self, function_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """执行函数调用"""
        if function_name == "search_web":
            return self.search_web(**arguments)
        else:
            return {
                "error": f"未知的函数: {function_name}"
            }
    
    def search_web(self, query: str, count: int = 5) -> Dict[str, Any]:
        """执行网络搜索"""
        try:
            results = self.web_search_api.search(query, count)
            
            if not results:
                return {
                    "error": "未找到搜索结果，建议用户直接访问相关官方网站获取最新信息"
                }
            
            # 格式化搜索结果
            formatted_results = []
            for i, result in enumerate(results, 1):
                formatted_results.append({
                    "序号": i,
                    "标题": result['title'],
                    "网址": result['url'],
                    "摘要": result['snippet'],
                    "来源": result['source']
                })
            
            return {
                "success": True,
                "query": query,
                "result_count": len(results),
                "results": formatted_results,
                "search_time": time.strftime("%Y-%m-%d %H:%M:%S")
            }
            
        except Exception as e:
            return {
                "error": f"搜索过程中出现错误: {str(e)}"
            }
    
    def configure_api_keys(self, bing_key: Optional[str] = None, 
                          google_key: Optional[str] = None, 
                          google_cx: Optional[str] = None):
        """配置API密钥"""
        if bing_key:
            self.web_search_api.bing_subscription_key = bing_key
        if google_key:
            self.web_search_api.google_api_key = google_key
        if google_cx:
            self.web_search_api.google_cx = google_cx
    
    def load_api_config(self, config_file: str = "search_api_config.json"):
        """从配置文件加载API密钥"""
        try:
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.configure_api_keys(
                        bing_key=config.get('bing_subscription_key'),
                        google_key=config.get('google_api_key'),
                        google_cx=config.get('google_cx')
                    )
        except Exception as e:
            print(f"加载API配置失败: {e}")


# 测试代码
if __name__ == "__main__":
    handler = FunctionCallHandler()
    
    # 测试搜索功能（使用DuckDuckGo免费API）
    result = handler.search_web("小米最新手机型号", 3)
    print("搜索结果:")
    print(json.dumps(result, ensure_ascii=False, indent=2))