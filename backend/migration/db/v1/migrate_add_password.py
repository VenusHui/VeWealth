"""
数据库迁移脚本：为 users 表添加 hashed_password 字段

运行方式：
python migrate_add_password.py
"""

from sqlalchemy import text
from app.core.database import engine


def migrate():
    """执行数据库迁移"""
    with engine.connect() as conn:
        try:
            # 检查字段是否已存在
            result = conn.execute(text("""
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name='users' AND column_name='hashed_password';
                """))

            if result.fetchone():
                print("✅ hashed_password 字段已存在，无需迁移")
                return

            # 添加 hashed_password 字段
            print("开始添加 hashed_password 字段...")
            conn.execute(text("""
                    ALTER TABLE users 
                    ADD COLUMN hashed_password VARCHAR(255);
                """))
            conn.commit()
            print("✅ 成功添加 hashed_password 字段")

            # 检查是否有现有用户
            result = conn.execute(text("SELECT COUNT(*) FROM users;"))
            user_count = result.fetchone()[0]

            if user_count > 0:
                print(
                    f"\n⚠️  警告：数据库中有 {user_count} 个现有用户。"
                    "\n这些用户的 hashed_password 字段为 NULL。"
                    "\n建议：让这些用户重新注册或手动设置他们的密码。"
                )
            else:
                print("\n✅ 数据库中没有现有用户，迁移完成")

        except Exception as e:
            print(f"❌ 迁移失败: {e}")
            conn.rollback()
            raise


if __name__ == "__main__":
    print("=" * 60)
    print("数据库迁移：添加 hashed_password 字段")
    print("=" * 60)
    migrate()
    print("=" * 60)
    print("迁移完成！")
    print("=" * 60)
