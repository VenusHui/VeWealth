"""
微信公众号服务
"""
from typing import Optional
from wechatpy import WeChatClient
from wechatpy.exceptions import WeChatClientException
from app.core.config import settings


class WeChatService:
    """微信公众号服务"""
    
    def __init__(self):
        if settings.WECHAT_APP_ID and settings.WECHAT_APP_SECRET:
            self.client = WeChatClient(
                settings.WECHAT_APP_ID,
                settings.WECHAT_APP_SECRET
            )
            self.enabled = True
        else:
            self.client = None
            self.enabled = False
            print("[微信服务] 未配置微信公众号参数，微信通知功能已禁用")
    
    def send_template_message(
        self,
        openid: str,
        template_id: str,
        data: dict,
        url: Optional[str] = None
    ) -> bool:
        """
        发送模板消息
        
        Args:
            openid: 用户OpenID
            template_id: 模板ID
            data: 模板数据
            url: 跳转链接
        
        Returns:
            是否发送成功
        """
        if not self.enabled:
            print("[微信服务] 微信通知功能未启用")
            return False
        
        try:
            self.client.message.send_template(
                user_id=openid,
                template_id=template_id,
                data=data,
                url=url
            )
            return True
        except WeChatClientException as e:
            print(f"[微信服务] 发送模板消息失败: {str(e)}")
            return False
    
    def send_price_alert(
        self,
        openid: str,
        stock_code: str,
        stock_name: str,
        current_price: float,
        alert_reason: str
    ) -> bool:
        """
        发送价格预警通知
        
        Args:
            openid: 用户OpenID
            stock_code: 股票代码
            stock_name: 股票名称
            current_price: 当前价格
            alert_reason: 预警原因
        
        Returns:
            是否发送成功
        """
        if not self.enabled:
            print(f"[微信服务] 模拟发送预警: {stock_name}({stock_code}) 当前价格 {current_price}, 原因: {alert_reason}")
            return True
        
        # 注意：这里需要在微信公众平台配置模板消息
        # 模板ID需要替换为实际申请的模板ID
        template_id = "your_template_id_here"
        
        data = {
            "first": {
                "value": "您关注的股票触发价格预警",
                "color": "#FF0000"
            },
            "keyword1": {
                "value": f"{stock_name}({stock_code})",
                "color": "#173177"
            },
            "keyword2": {
                "value": f"¥{current_price:.2f}",
                "color": "#FF0000"
            },
            "keyword3": {
                "value": alert_reason,
                "color": "#173177"
            },
            "remark": {
                "value": "请及时关注市场动态",
                "color": "#173177"
            }
        }
        
        return self.send_template_message(openid, template_id, data)


# 全局微信服务实例
wechat_service = WeChatService()

