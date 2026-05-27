"""
数据库迁移脚本：为 backtest_rounds 表添加 stock_name 字段

运行方式：
python migrate_add_backtest_round_stock_name.py
"""

from sqlalchemy import text

from app.core.database import engine


def migrate():
    """执行数据库迁移"""
    with engine.connect() as conn:
        try:
            result = conn.execute(text("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name='backtest_rounds' AND column_name='stock_name';
                    """))

            if result.fetchone():
                print("✅ stock_name 字段已存在，无需迁移")
                return

            print("开始添加 backtest_rounds.stock_name 字段...")
            conn.execute(text("""
                    ALTER TABLE backtest_rounds
                    ADD COLUMN stock_name VARCHAR(100);
                    """))
            conn.commit()
            print("✅ 成功添加 stock_name 字段")
        except Exception as e:
            print(f"❌ 迁移失败: {e}")
            conn.rollback()
            raise


if __name__ == "__main__":
    print("=" * 60)
    print("数据库迁移：添加 backtest_rounds.stock_name 字段")
    print("=" * 60)
    migrate()
    print("=" * 60)
    print("迁移完成！")
    print("=" * 60)
