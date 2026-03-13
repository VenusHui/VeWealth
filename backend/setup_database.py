"""
数据库初始化脚本
"""

import sys
from app.core.database import init_db, engine
from app.models import User, WatchList, StockMinuteData
from sqlalchemy import text


def check_connection():
    """检查数据库连接"""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        print("✓ 数据库连接成功")
        return True
    except Exception as e:
        print(f"✗ 数据库连接失败: {str(e)}")
        return False


def initialize_database():
    """初始化数据库表"""
    try:
        print("\n开始初始化数据库...")
        init_db()
        print("✓ 数据库表创建成功")
        return True
    except Exception as e:
        print(f"✗ 数据库表创建失败: {str(e)}")
        return False


def show_tables():
    """显示已创建的表"""
    try:
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public'
                ORDER BY table_name
            """))
            tables = [row[0] for row in result]

        if tables:
            print("\n已创建的数据表:")
            for table in tables:
                print(f"  - {table}")
        else:
            print("\n未找到数据表")

    except Exception as e:
        print(f"\n查询表失败: {str(e)}")


def main():
    """主函数"""
    print("=" * 50)
    print("VeWealth 数据库初始化工具")
    print("=" * 50)

    # 检查连接
    if not check_connection():
        print("\n请检查:")
        print("1. PostgreSQL 是否已安装并运行")
        print("2. 数据库配置是否正确（.env 文件或环境变量）")
        print("3. 数据库 'vewealth' 是否已创建")
        sys.exit(1)

    # 初始化数据库
    if not initialize_database():
        sys.exit(1)

    # 显示创建的表
    show_tables()

    print("\n" + "=" * 50)
    print("✓ 数据库初始化完成！")
    print("=" * 50)
    print("\n下一步:")
    print("1. 运行后端服务: python main.py")
    print("2. 访问 API 文档: http://localhost:8001/docs")
    print("3. 使用主密钥注册第一个用户")


if __name__ == "__main__":
    main()
