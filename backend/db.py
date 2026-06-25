import mysql.connector

def get_db_connection():
    try:
        conn = mysql.connector.connect(
            host="localhost",
            user="root",
            password="nithi_subha_71",
            database="cycletest"
        )
        return conn
    except Exception as e:
        print("DB Error:", e)
        return None