import psycopg2
import os


class PGFlyway:
    """
    Postgres Flyway utilities
    """

    def __init__(self, dbname="postgres"):
        """
        Initialization
        """
        self.flyway_path = os.path.join(os.path.dirname(__file__), "flyway")
        self.secrets_path = os.path.join(os.path.dirname(__file__), "secrets")
        self.db_host = open(os.path.join(self.secrets_path, "db_host.txt"), 'r').read().strip()
        self.db_user = open(os.path.join(self.secrets_path, "db_user.txt"), 'r').read().strip()
        try:
            self.db_password = open(os.path.join(self.secrets_path, "db_password.txt"), 'r').read().strip()
        except FileNotFoundError:
            raise Exception("Please create a file secrets/db_password.txt")
        self.db_port = open(os.path.join(self.secrets_path, "db_port.txt"), 'r').read().strip()
        try:
            # Connect to the default 'postgres' database to create a new one
            self.conn = psycopg2.connect(
                host=self.db_host,
                user=self.db_user,
                password=self.db_password,
                dbname=dbname,
                port=self.db_port  # Connect to a default database to create others
            )
        except psycopg2.Error as e:
            raise Exception(f"Error creating database: {e}")

    def create_database(self, db_name: str):
        """
        Create a database if it doesn't exist
        :param db_name: name of new database
        """
        self.conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)
        self.conn.cursor().execute("SELECT 1 FROM pg_catalog.pg_database WHERE datname = '{}'".format(db_name))
        try:
            exists = self.conn.cursor().fetchone()
        except psycopg2.ProgrammingError:
            try:
                self.conn.cursor().execute("CREATE DATABASE {}".format(db_name))
            except psycopg2.errors.DuplicateDatabase:
                print(f"{db_name} already exists.")

    def create_table(self, table_name: str):
        """
        Create a postgres table from create table script saved in a file
        :param table_name: name of the table
        """
        flyway_script_name = f"create_table_{table_name}.sql"
        create_table_statement = open(os.path.join(self.flyway_path, flyway_script_name), 'r').read()
        try:
            self.conn.cursor().execute(create_table_statement)
        except psycopg2.errors.DuplicateTable:
            print(f"Relation \"{table_name}\" was already created")
