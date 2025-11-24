#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ASP.NET API Help页面转Swagger JSON工具
用法: python aspnet_to_swagger.py <help_url>
示例: python aspnet_to_swagger.py http://47.94.10.219/Help
"""

import re
import json
import sys
import argparse
from urllib.parse import urljoin, urlparse, parse_qs
from typing import Dict, List, Any, Optional
import requests
from bs4 import BeautifulSoup


class AspNetToSwagger:
    """ASP.NET API转Swagger转换器"""
    
    def __init__(self, base_url: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip('/')
        self.verify_ssl = verify_ssl
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        # 禁用SSL警告
        if not verify_ssl:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
    def fetch_page(self, url: str) -> Optional[str]:
        """获取页面内容"""
        try:
            response = self.session.get(url, timeout=30, verify=self.verify_ssl)
            response.raise_for_status()
            response.encoding = 'utf-8'
            return response.text
        except Exception as e:
            print(f"获取页面失败 {url}: {e}", file=sys.stderr)
            return None
    
    def parse_main_page(self, html: str) -> Dict[str, Dict]:
        """解析主Help页面,提取API列表"""
        soup = BeautifulSoup(html, 'html.parser')
        api_groups = {}
        
        # 查找所有API分组
        for h2 in soup.find_all('h2', id=True):
            group_name = h2.get('id')
            if not group_name:
                continue
            
            # 提取分组描述(可能在h2后面的p标签中)
            group_description = ''
            next_elem = h2.find_next_sibling()
            if next_elem and next_elem.name == 'p':
                group_description = next_elem.get_text(strip=True)
                
            # 查找该分组下的表格
            table = h2.find_next('table', class_='help-page-table')
            if not table:
                continue
            
            apis = []
            tbody = table.find('tbody')
            if tbody:
                for row in tbody.find_all('tr'):
                    api_link = row.find('a')
                    if api_link:
                        desc_td = row.find('td', class_='api-documentation')
                        api_info = {
                            'name': api_link.get_text(strip=True),
                            'url': urljoin(self.base_url, api_link.get('href', '')),
                            'description': desc_td.get_text(strip=True) if desc_td else 'No documentation available.'
                        }
                        apis.append(api_info)
            
            if apis:
                api_groups[group_name] = {
                    'description': group_description,
                    'apis': apis
                }
        
        return api_groups
    
    def parse_api_detail(self, url: str) -> Optional[Dict]:
        """解析API详情页面"""
        html = self.fetch_page(url)
        if not html:
            return None
        
        soup = BeautifulSoup(html, 'html.parser')
        api_detail = {
            'parameters': [],
            'request_body': None,
            'responses': {}
        }
        
        # 提取API名称和描述
        title = soup.find('h1')
        if title:
            api_detail['title'] = title.get_text(strip=True)
        
        # 提取URI参数
        uri_params_section = soup.find('h2', string=re.compile('URI参数|URI Parameters', re.I))
        if uri_params_section:
            table = uri_params_section.find_next('table')
            if table:
                for row in table.find_all('tr')[1:]:  # 跳过表头
                    cols = row.find_all('td')
                    if len(cols) >= 3:
                        param = {
                            'name': cols[0].get_text(strip=True),
                            'type': cols[1].get_text(strip=True),
                            'description': cols[2].get_text(strip=True),
                            'in': 'query'
                        }
                        api_detail['parameters'].append(param)
        
        # 提取请求体信息
        request_section = soup.find('h2', string=re.compile('请求正文|Request Body', re.I))
        if request_section:
            # 查找示例JSON
            pre = request_section.find_next('pre')
            if pre:
                try:
                    api_detail['request_body'] = pre.get_text(strip=True)
                except:
                    pass
        
        # 提取响应信息
        response_section = soup.find('h2', string=re.compile('响应|Response', re.I))
        if response_section:
            pre = response_section.find_next('pre')
            if pre:
                api_detail['responses']['200'] = {
                    'description': 'Success',
                    'example': pre.get_text(strip=True)
                }
        
        return api_detail
    
    def extract_path_and_method(self, api_name: str) -> tuple:
        """从API名称中提取HTTP方法和路径"""
        # 格式1: "GET Default/FaceOrdering/GetInterval?machine_Number={machine_Number}"
        # 格式2: "GET api/products/suggest/adv?query={query}"
        # 格式3: "POST api/product/edit/book/{product_id}?token={token}"
        match = re.match(r'^(GET|POST|PUT|DELETE|PATCH)\s+(.+?)(?:\?|$)', api_name, re.IGNORECASE)
        if match:
            method = match.group(1).lower()
            path_raw = match.group(2).strip()
            
            # 保留路径参数{xxx},但转换为标准格式
            # 提取路径参数
            path_params = re.findall(r'\{([^}]+)\}', path_raw)
            
            # 构建路径,保留路径参数
            path = '/' + path_raw.strip('/')
            
            return method, path
        return 'get', '/unknown'
    
    def extract_parameters_from_name(self, api_name: str) -> List[Dict]:
        """从API名称中提取参数"""
        parameters = []
        seen_params = set()  # 用于去重
        
        # 提取路径参数(优先处理,因为路径参数是必需的)
        path_params = re.findall(r'/\{([^}]+)\}', api_name)
        for param in path_params:
            if param not in seen_params:
                parameters.append({
                    'name': param,
                    'in': 'path',
                    'required': True,
                    'schema': {'type': 'string'},
                    'description': f'路径参数 {param}'
                })
                seen_params.add(param)
        
        # 提取查询参数
        if '?' in api_name:
            query_part = api_name.split('?', 1)[1]
            # 匹配 param={param} 格式
            param_matches = re.findall(r'(\w+)=\{([^}]+)\}', query_part)
            for param_name, param_placeholder in param_matches:
                if param_name not in seen_params:
                    parameters.append({
                        'name': param_name,
                        'in': 'query',
                        'required': False,
                        'schema': {'type': 'string'},
                        'description': f'查询参数 {param_name}'
                    })
                    seen_params.add(param_name)
        
        return parameters
    
    def convert_to_swagger(self, api_groups: Dict[str, Dict]) -> Dict:
        """转换为Swagger JSON格式"""
        swagger = {
            'openapi': '3.0.0',
            'info': {
                'title': 'ASP.NET Web API',
                'description': '从ASP.NET Help页面转换的API文档',
                'version': '1.0.0'
            },
            'servers': [
                {
                    'url': self.base_url.replace('/Help', '').replace('/help', ''),
                    'description': 'API服务器'
                }
            ],
            'tags': [],
            'paths': {}
        }
        
        # 添加标签
        for group_name, group_data in api_groups.items():
            tag_desc = group_data.get('description', f'{group_name} 相关接口')
            swagger['tags'].append({
                'name': group_name,
                'description': tag_desc if tag_desc else f'{group_name} 相关接口'
            })
        
        # 处理每个API
        operation_id_counter = {}  # 用于处理重复的operationId
        
        for group_name, group_data in api_groups.items():
            apis = group_data.get('apis', [])
            for api in apis:
                method, path = self.extract_path_and_method(api['name'])
                
                # 初始化路径
                if path not in swagger['paths']:
                    swagger['paths'][path] = {}
                
                # 提取参数
                parameters = self.extract_parameters_from_name(api['name'])
                
                # 生成唯一的operationId
                base_operation_id = f"{method}_{path.replace('/', '_').replace('{', '').replace('}', '').strip('_')}"
                operation_id = base_operation_id
                
                # 处理operationId冲突
                if operation_id in operation_id_counter:
                    operation_id_counter[operation_id] += 1
                    operation_id = f"{base_operation_id}_{operation_id_counter[operation_id]}"
                else:
                    operation_id_counter[operation_id] = 0
                
                # 构建操作对象
                operation = {
                    'tags': [group_name],
                    'summary': api['name'],
                    'description': api.get('description', 'No documentation available.'),
                    'operationId': operation_id,
                    'responses': {
                        '200': {
                            'description': '成功响应',
                            'content': {
                                'application/json': {
                                    'schema': {
                                        'type': 'object'
                                    }
                                }
                            }
                        },
                        '400': {
                            'description': '请求错误'
                        },
                        '500': {
                            'description': '服务器错误'
                        }
                    }
                }
                
                # 添加参数(如果有)
                if parameters:
                    operation['parameters'] = parameters
                
                # 如果是POST/PUT/PATCH,添加请求体
                if method in ['post', 'put', 'patch']:
                    operation['requestBody'] = {
                        'content': {
                            'application/json': {
                                'schema': {
                                    'type': 'object'
                                }
                            }
                        }
                    }
                
                swagger['paths'][path][method] = operation
        
        return swagger
    
    def run(self, output_file: Optional[str] = None, fetch_details: bool = False):
        """执行转换"""
        print(f"正在获取主页面: {self.base_url}")
        html = self.fetch_page(self.base_url)
        if not html:
            print("无法获取主页面", file=sys.stderr)
            return False
        
        print("正在解析API列表...")
        api_groups = self.parse_main_page(html)
        
        total_apis = sum(len(group_data.get('apis', [])) for group_data in api_groups.values())
        print(f"发现 {len(api_groups)} 个API分组,共 {total_apis} 个接口")
        
        if fetch_details:
            print("正在获取API详情(可能需要较长时间)...")
            for group_name, group_data in api_groups.items():
                apis = group_data.get('apis', [])
                for i, api in enumerate(apis, 1):
                    print(f"  [{group_name}] {i}/{len(apis)}: {api['name']}")
                    detail = self.parse_api_detail(api['url'])
                    if detail:
                        api['detail'] = detail
        
        print("正在生成Swagger JSON...")
        swagger_json = self.convert_to_swagger(api_groups)
        
        # 输出结果
        json_str = json.dumps(swagger_json, ensure_ascii=False, indent=2)
        
        if output_file:
            with open(output_file, 'w', encoding='utf-8') as f:
                f.write(json_str)
            print(f"\n✓ Swagger JSON已保存到: {output_file}")
        else:
            print("\n" + "="*80)
            print(json_str)
            print("="*80)
        
        return True


def main():
    parser = argparse.ArgumentParser(
        description='将ASP.NET API Help页面转换为Swagger JSON格式,By 知攻善防实验室 ChinaRan404',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  %(prog)s http://47.94.10.219/Help
  %(prog)s http://47.94.10.219/Help -o swagger.json
  %(prog)s http://47.94.10.219/Help -o swagger.json --fetch-details
        """
    )
    
    parser.add_argument('url', help='ASP.NET Help页面URL')
    parser.add_argument('-o', '--output', help='输出文件路径(默认输出到控制台)')
    parser.add_argument('--fetch-details', action='store_true', 
                       help='获取每个API的详细信息(较慢)')
    parser.add_argument('--no-verify-ssl', action='store_true',
                       help='禁用SSL证书验证(用于自签名证书)')
    
    args = parser.parse_args()
    
    verify_ssl = not args.no_verify_ssl
    converter = AspNetToSwagger(args.url, verify_ssl=verify_ssl)
    success = converter.run(args.output, args.fetch_details)
    
    sys.exit(0 if success else 1)


if __name__ == '__main__':
    main()
