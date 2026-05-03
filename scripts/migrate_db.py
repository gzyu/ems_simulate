"""
数据库迁移脚本: 
1. 扩大 reg_addr 列从 VARCHAR(32) 到 VARCHAR(128) (支持 IEC61850 BDA 路径)
2. 添加 fc 列到 point_yc/yx/yk/yt 表 (支持 IEC61850 FC 持久化)

用法: python scripts/migrate_db.py
"""
import sqlite3
import os
import sys

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "ems.db")

TABLES = ["point_yc", "point_yx", "point_yk", "point_yt"]


def migrate():
    if not os.path.exists(DB_PATH):
        print(f"数据库文件不存在: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    for table in TABLES:
        print(f"\n--- 迁移表: {table} ---")

        # 检查 reg_addr 列当前类型
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()
        col_info = {c[1]: c for c in columns}

        # 1. 扩大 reg_addr 列
        if "reg_addr" in col_info:
            current_type = col_info["reg_addr"][2]
            print(f"  reg_addr 当前类型: {current_type}")
            if "32" in current_type:
                # SQLite 不支持 ALTER COLUMN, 需要重建表
                print(f"  需要扩大 reg_addr: VARCHAR(32) → VARCHAR(128)")
                _rebuild_table_with_wider_reg_addr(cursor, table)
            else:
                print(f"  reg_addr 类型已兼容, 无需修改")

        # 2. 添加 fc 列
        if "fc" not in col_info:
            print(f"  添加 fc 列...")
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN fc VARCHAR(8)")
            print(f"  fc 列已添加")
        else:
            print(f"  fc 列已存在, 无需添加")

    conn.commit()
    conn.close()
    print("\n迁移完成!")


def _rebuild_table_with_wider_reg_addr(cursor, table):
    """SQLite 不支持 ALTER COLUMN, 需要通过重建表来修改列宽度"""
    # 获取建表 SQL
    cursor.execute(f"SELECT sql FROM sqlite_master WHERE type='table' AND name='{table}'")
    row = cursor.fetchone()
    if not row:
        print(f"  警告: 找不到表 {table} 的建表语句")
        return

    original_sql = row[0]
    # 替换 VARCHAR(32) 为 VARCHAR(128) (仅对 reg_addr 列)
    # 使用更精确的替换, 确保只替换 reg_addr 行的
    new_sql = original_sql.replace('"reg_addr" VARCHAR(32)', '"reg_addr" VARCHAR(128)')

    if new_sql == original_sql:
        # 可能没有引号
        new_sql = original_sql.replace('reg_addr VARCHAR(32)', 'reg_addr VARCHAR(128)')

    if new_sql == original_sql:
        print(f"  警告: 未找到 reg_addr VARCHAR(32) 模式, 跳过")
        return

    temp_table = f"_temp_{table}"

    try:
        # 获取所有列名
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()
        col_names = [c[1] for c in columns]

        # 1. 用新 schema 创建临时表
        cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
        cursor.execute(new_sql.replace(table, temp_table, 1))

        # 2. 复制数据
        cols_str = ", ".join(f'"{c}"' for c in col_names)
        cursor.execute(f"INSERT INTO {temp_table} ({cols_str}) SELECT {cols_str} FROM {table}")

        # 3. 删除旧表
        cursor.execute(f"DROP TABLE {table}")

        # 4. 重命名临时表
        cursor.execute(f"ALTER TABLE {temp_table} RENAME TO {table}")

        print(f"  reg_addr 列已扩大为 VARCHAR(128)")
    except Exception as e:
        print(f"  重建表失败: {e}")
        # 清理临时表
        cursor.execute(f"DROP TABLE IF EXISTS {temp_table}")
        raise


if __name__ == "__main__":
    migrate()
