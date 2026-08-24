"""
微信公众号服务
"""

from typing import Optional
from wechatpy import WeChatClient
from wechatpy.exceptions import WeChatClientException
from app.core.config import settings
from app.core.logger import get_module_logger

# 获取logger
logger = get_module_logger("wechat_service")


class WeChatService:
    """微信公众号服务"""

    def __init__(self):
        if settings.WECHAT_APP_ID and settings.WECHAT_APP_SECRET:
            self.client = WeChatClient(
                settings.WECHAT_APP_ID, settings.WECHAT_APP_SECRET
            )
            self.enabled = True
            logger.info("微信服务已启用")
        else:
            self.client = None
            self.enabled = False
            logger.info("未配置微信公众号参数，微信通知功能已禁用")

    def send_template_message(
        self, openid: str, template_id: str, data: dict, url: Optional[str] = None
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
            logger.warning("微信通知功能未启用")
            return False

        try:
            self.client.message.send_template(
                user_id=openid, template_id=template_id, data=data, url=url
            )
            logger.info(f"成功发送模板消息给用户 {openid}")
            return True
        except WeChatClientException as e:
            logger.error(f"发送模板消息失败: {str(e)}", exc_info=True)
            return False

    def send_price_alert(
        self,
        openid: str,
        stock_code: str,
        stock_name: str,
        current_price: float,
        alert_reason: str,
        alert_direction: Optional[str] = None,
    ) -> bool:
        """
        发送价格预警通知

        Args:
            openid: 用户OpenID
            stock_code: 股票代码
            stock_name: 股票名称
            current_price: 当前价格
            alert_reason: 预警原因
            alert_direction: 预警方向 buy / sell

        Returns:
            是否发送成功
        """
        if not self.enabled:
            direction_label = ""
            if alert_direction == "buy":
                direction_label = "买入"
            elif alert_direction == "sell":
                direction_label = "卖出"
            logger.info(
                f"模拟发送预警: {stock_name}({stock_code}) 当前价格 {current_price}, "
                f"方向: {direction_label or 'N/A'}, 原因: {alert_reason}"
            )
            return True

        # 注意：这里需要在微信公众平台配置模板消息
        # 模板ID需要替换为实际申请的模板ID
        template_id = "your_template_id_here"

        if alert_direction == "buy":
            title = "您关注的股票触发买入信号"
            price_color = "#dc2626"
        elif alert_direction == "sell":
            title = "您关注的股票触发卖出信号"
            price_color = "#16a34a"
        else:
            title = "您关注的股票触发价格预警"
            price_color = "#FF0000"

        data = {
            "first": {"value": title, "color": price_color},
            "keyword1": {"value": f"{stock_name}({stock_code})", "color": "#173177"},
            "keyword2": {"value": f"¥{current_price:.2f}", "color": price_color},
            "keyword3": {"value": alert_reason, "color": "#173177"},
            "remark": {"value": "请及时关注市场动态", "color": "#173177"},
        }

        return self.send_template_message(openid, template_id, data)


# 全局微信服务实例
wechat_service = WeChatService()
