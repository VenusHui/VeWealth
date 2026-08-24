"""
数据库迁移脚本：为 alert_history 表添加 alert_direction, density_value, peak_price 字段

运行方式：
python migrate_add_alert_direction.py
"""

from sqlalchemy import text
from app.core.database import engine


def migrate():
    """执行数据库迁移"""
    with engine.connect() as conn:
        try:
            # 检查 alert_direction 字段是否已存在
            result = conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name='alert_history' AND column_name='alert_direction';
                """))

            if result.fetchone():
                print("alert_direction 字段已存在，无需迁移")
                return

            print("开始添加 alert_direction, density_value, peak_price 字段...")
            conn.execute(text("""
                    ALTER TABLE alert_history
                    ADD COLUMN alert_direction VARCHAR(4),
                    ADD COLUMN density_value FLOAT,
                    ADD COLUMN peak_price FLOAT;
                """))
            conn.commit()
            print("成功添加 alert_direction, density_value, peak_price 字段")

            # 检查现有记录数
            result = conn.execute(text("SELECT COUNT(*) FROM alert_history;"))
            alert_count = result.fetchone()[0]
            if alert_count > 0:
                print(
                    f"\n注意：数据库中有 {alert_count} 条现有预警记录。"
                    "\n这些记录的新字段为 NULL，不影响正常使用。"
                )

        except Exception as e:
            print(f"迁移失败: {e}")
            conn.rollback()
            raise


if __name__ == "__main__":
    print("=" * 60)
    print("数据库迁移：添加 alert_direction 等字段")
    print("=" * 60)
    migrate()
    print("=" * 60)
    print("迁移完成！")
    print("=" * 60)
