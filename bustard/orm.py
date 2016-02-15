# -*- coding: utf-8 -*-
import abc
import collections

import psycopg2


class MetaData:
    tables = collections.OrderedDict()
    indexes = []

    @classmethod
    def index_sqls(cls):
        sqls = []
        for index in cls.indexes:
            sqls.append(index.to_sql())
        return ';\n'.join(sqls)

    @classmethod
    def create_all(cls, bind):
        connection = bind.connect()
        cursor = connection.cursor()
        for model in cls.tables.values():
            sql = model.table_sql()
            cursor.execute(sql)
        index_sql = cls.index_sqls()
        if index_sql:
            cursor.execute(index_sql)
        connection.commit()
        cursor.close()
        connection.close()

    @classmethod
    def drop_all(cls, bind):
        connection = bind.connect()
        cursor = connection.cursor()
        for table_name in cls.tables:
            cursor.execute('DROP TABLE IF EXISTS {}'.format(table_name))
        connection.commit()
        cursor.close()
        connection.close()


class Field(metaclass=abc.ABCMeta):
    def __init__(self, name=None, max_length=None, default=None,
                 server_default=None, unique=False, nullable=True,
                 index=False, primary_key=False, foreign_key=None):
        self.name = name
        self.max_length = max_length
        self.default = default
        if server_default is True or server_default is False:
            server_default = str(server_default).upper()
        self.server_default = server_default
        self.unique = unique
        self.nullable = nullable
        self.index = index
        self.primary_key = primary_key
        self.foreign_key = foreign_key

    def __get__(self, instance, owner):
        if instance is None:
            return self
        if self._name not in instance.__dict__:
            value = getattr(owner, self._name)
            return value.default_value()
        else:
            return instance.__dict__[self._name]

    def __set__(self, instance, value):
        instance.__dict__[self._name] = value

    def __lt__(self, value):
        return '{.sql_column} < %s'.format(self), value

    def __eq__(self, value):
        return '{sql_column} = %s'.format(self), value

    def __ne__(self, value):
        return '{.sql_column} != %s'.format(self), value

    def __gt__(self, value):
        return '{.sql_column} > %s'.format(self), value

    def like(self, value):
        return '{.sql_column} LIKE %s'.format(self), value

    @property
    def desc(self):
        return '{.sql_column} DESC'.format(self)

    def to_sql(self):
        sql = '{0.name} {0.data_type}'.format(self)
        if self.max_length is not None:
            sql += '({0.max_length})'.format(self)
        if self.server_default:
            sql += ' DEFAULT {0.server_default}'.format(self)
        if not self.nullable:
            sql += ' NOT NULL'
        if self.unique:
            sql += ' UNIQUE'
        if self.primary_key:
            sql += ' PRIMARY KEY'
        if self.foreign_key is not None:
            sql += ' {}'.format(self.foreign_key.to_sql())
        return sql

    def default_value(self):
        value = self.default
        if callable(self.default):
            value = self.default()
        if self.default is None:
            if isinstance(self, TextField):
                value = 'None'
        return value


def _collect_fields(attr_dict, model):
    fields = []
    for field in attr_dict.values():
        if isinstance(field, Field):
            fields.append(field)
            field.model = model
            field.table_name = model.table_name
            field.sql_column = '{}.{}'.format(field.table_name, field.name)
    return fields


def _get_table_name(attr_dict):
    return attr_dict.get('__tablename__')


def _auto_column_name(attr_dict):
    for attr, attr_value in attr_dict.items():
        if isinstance(attr_value, Field):
            attr_value._name = attr
            if attr_value.name is None:
                attr_value.name = attr


def _collect_indexes(table_name, attr_dict):
    indexes = []
    for attr, attr_value in attr_dict.items():
        if isinstance(attr_value, Field):
            if not any([attr_value.unique, attr_value.primary_key,
                        attr_value.foreign_key]) and attr_value.index:
                indexes.append(attr_value)
    for field in indexes:
        column_name = field.name
        name = 'index_{}_{}'.format(table_name, column_name)
        index = Index(name, table_name, column_name, unique=field.unique)
        MetaData.indexes.append(index)


class ModelMetaClass(type):

    def __init__(cls, name, bases, attr_dict):
        table_name = _get_table_name(attr_dict)
        if table_name:
            cls.table_name = table_name
            MetaData.tables[table_name] = cls
            _auto_column_name(attr_dict)
            cls.fields = _collect_fields(attr_dict, cls)
            for field in cls.fields:
                if field.primary_key:
                    cls.pk_name = field.name
                    break
            _collect_indexes(table_name, attr_dict)

    @classmethod
    def __prepare__(cls, name, bases):
        """让 attr_dict 有序"""
        return collections.OrderedDict()


class Model(metaclass=ModelMetaClass):

    def __init__(self, **kwargs):
        self.__dict__.update(self.default_dict())
        for kwg, value in kwargs.items():
            setattr(self, kwg, value)
        for field in self.fields:
            if field.primary_key:
                self.pk_field = field
                break

    def default_dict(self):
        return {field._name: field.default_value() for field in self.fields}

    @classmethod
    def table_sql(cls):
        column_sqls = ',\n    '.join(field.to_sql() for field in cls.fields)
        sql = '''
CREATE TABLE {table_name} (
    {column_sqls}
);
'''.format(table_name=cls.table_name, column_sqls=column_sqls)
        return sql

    def sql_values(self):
        values_dict = collections.OrderedDict()
        for field in self.fields:
            if field is self.pk_field:
                continue
            value = getattr(self, field._name, field.default_value())
            if value is not None:
                values_dict[field.name] = value
        return values_dict


class TextField(Field):
    data_type = 'text'


class CharField(TextField):
    data_type = 'varchar'

    def __init__(self, max_length=None, **kwargs):
        super(CharField, self).__init__(max_length=max_length, **kwargs)


class IntegerField(Field):
    data_type = 'integer'


class DateField(Field):
    data_type = 'date'


class DateTimeField(Field):
    data_type = 'timestamp'


class BooleanField(Field):
    data_type = 'boolean'


class UUIDField(Field):
    data_type = 'uuid'


class JSONField(Field):
    data_type = 'json'


class AutoField(Field):
    data_type = 'serial'


class ForeignKey:
    """

    :param column: A single target column for the key relationship.
                   A column name as a string: `table_name.column_name`
    :param onupdate: Optional string. If set, emit ON UPDATE <value>
                     when issuing DDL for this constraint. Typical values
                     include CASCADE, DELETE and RESTRICT.
    :param ondelete: Optional string. If set, emit ON DELETE <value>
                     when issuing DDL for this constraint. Typical values
                     include CASCADE, DELETE and RESTRICT.
    """
    def __init__(self, column, onupdate=None, ondelete=None):
        self.table_name, self.column_name = column.split('.')
        self.onupdate = onupdate
        self.ondelete = ondelete

    def to_sql(self):
        sql = 'REFERENCES {0.table_name} ({0.column_name})'.format(self)
        if self.onupdate:
            sql += ' ON UPDATE {0.onupdate}'.format(self)
        if self.ondelete:
            sql += ' ON DELETE {0.ondelete}'.format(self)
        return sql


class Index:
    def __init__(self, name, table_name, column_name, unique=False):
        self.name = name
        self.table_name = table_name
        self.column_name = column_name
        self.unique = unique

    def to_sql(self):
        sql = 'CREATE'
        if self.unique:
            sql += ' UNIQUE'
        sql += (' INDEX {0.name} ON {0.table_name} ({0.column_name})'
                ).format(self)
        return sql + ';'


class Engine:

    def __init__(self, uri):
        self.uri = uri

    def connect(self):
        self.connection = psycopg2.connect(self.uri)
        return self.connection

    def close(self):
        self.connection.close()


class Session:
    _bind = None

    def __init__(self, bind=None):
        self.bind = bind or self._bind
        if self.bind is not None:
            self.connect()

    @classmethod
    def configure(cls, bind):
        cls._bind = bind

    def connect(self):
        self.connection = self.bind.connect()
        self.cursor = self.connection.cursor()

    def execute(self, sql, args):
        return self.cursor.execute(sql, args)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchmany(self, size=None):
        if size is None:
            return self.cursor.fetchmany()
        else:
            return self.cursor.fetchmany(size)

    def fetchall(self):
        return self.cursor.fetchall()

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.cursor.close()
        self.connection.close()

    def insert(self, instance):
        pk_name = instance.pk_field.name
        sql_values = instance.sql_values()
        columns = ', '.join('{0}'.format(k) for k in sql_values)
        values = ', '.join('%s' for k in sql_values)
        sql = (
            'INSERT INTO {table_name} ({columns}) VALUES ({values}) '
            'RETURNING {pk_name};').format(
            table_name=instance.table_name, columns=columns,
            values=values, pk_name=pk_name
        )
        args = list(sql_values.values())
        self.execute(sql, args)
        pk_value = self.fetchone()[0]
        setattr(instance, instance.pk_field._name, pk_value)

    def update(self, instance):
        pk_name = instance.pk_field.name
        pk_value = getattr(instance, pk_name)
        sql_values = instance.sql_values()
        columns = ', '.join('{0} = %s'.format(k) for k in sql_values)
        sql = 'UPDATE {table_name} SET {columns} WHERE {pk_name} = %s;'.format(
            table_name=instance.table_name, pk_name=pk_name,
            columns=columns
        )
        args = list(sql_values.values()) + [pk_value]
        self.execute(sql, args)

    def delete(self, instance):
        pk_name = instance.pk_field.name
        pk_value = getattr(instance, pk_name)
        sql = 'DELETE FROM {table_name} WHERE {pk_name} = %s'.format(
            table_name=instance.table_name, pk_name=pk_name
        )
        args = (pk_value,)
        self.execute(sql, args)
        setattr(instance, pk_name, None)


class QuerySet:

    def __init__(self, session, model, exps=None):
        self.session = session
        self.model = model
        self.exps = exps or []

    def filter(self, *args, **kwargs):
        pass

    def _build_sql(self):
        table_name = self.model.table_name
        where = 'AND '.join(x[0] for x in self.exps)
        args = [x[1] for x in self.exps]
        if where:
            where = 'WHERE ' + where
        column_names = ', '.join(
            '{column_name} AS {table_name}_{column_name}'.format(
                column_name=field.name, table_name=table_name
            )
            for field in self.models.fields
        )
        return ('SELECT {column_names} FROM {table_name} {where};'.format(
                column_names=column_names, table_name=table_name, where=where
                ), args)

    def __iter__(self):
        sql, args = self._build_sql()
        self.session.execute(sql, args)
        for row in self.session.fetchall():
            instance = self.model()
            for nu, value in enumerate(row):
                setattr(instance, self.model.fields[nu].name, value)
            yield instance
