import hashlib
from alembic.config import Config
from alembic import command
from flask import current_app
import sys
import os
import importlib

from .file_base_manager import file_base_manager, file_scanner, class_in_file
from .models import *

class table_Context:
    def __init__(self, file_info:file_scanner, table_info=None):
        self.file_info = file_info
        self.table_info = table_info
        # 导入模型类
        module = importlib.import_module('.models.{0}'.format(self.file_info.file_name.split('.')[0]), 'DataService')
        self.class_ = getattr(module, self.file_info.name)

class table_manager(file_base_manager):

    def __init__(self, mod=None):
        file_base_manager.__init__(self)
        self.current_mod = mod
        self.mapping_filename = None

    def set_mod(self, mod):
        self.current_mod = mod

    def set_current_mod(self, mod):
        """
        设置当前模块管理器
        :param mod:
        :return:
        """
        self.current_mod = mod

    # -----register table--------
    def register_table(self, table_info):
        """
        注册私有表
        note: 注册后表进入待审核状态
        :param table_info: [...
                {
                    table_name:
                    sensitivity:
                    primary_key: []
                    description:
                    note:
                    fields: {
                        name: {
                            type:
                            length:
                            sensitivity： # 敏感度
                            description:
                            note:
                            nullable:
                            unique:
                            default: # 待支持
                            foreignkey:{
                                table:,
                                field:
                            }
                        }
                    }
                }
        :return:
        """
        # 合法性校验
        self.check_table_info(table_info)
        for table in table_info:
            sensitivity = int(table.get('sensitivity', 0))
            table_row = Tables(name=table['table_name'], owner_id=self.current_mod.get('id'), status=0, sensitivity=sensitivity)
            self.session.add(table_row)
            self.session.commit() # 获取id
            for field in table['fields']:
                f_sensitivity = int(table['fields'][field].get('sensitivity', 0))
                field_row = Fields(name=field, table_id=table_row.id, sensitivity=f_sensitivity)
                self.session.add(field_row)
            try:
                self.session.commit()
            except:
                self.session.rollback()
        self.create_table(table_info)

    def create_table(self, table_info, update=True):
        """
        生成私有表
        TODO：
            - 文件可用性测试
            - __init__重复导入校验
            - 表属性记录
            - default参数
            - 注释
        :param update: 是否更新数据库
        :param table_info:
        :return:
        """
        # 文件生成
        # 语句模板
        tab = '    '
        import_template = 'from {package} import {statement}\n'
        class_template = 'class {table_name}(Base):\n'
        # 文件名hash
        m = hashlib.md5()
        m.update(self.current_mod.get('name').encode())
        self.mapping_filename = m.hexdigest()
        if not formating_check(self.mapping_filename):
            self.mapping_filename = '_' + self.mapping_filename

        with open('models/{}.py'.format(self.mapping_filename), 'a+') as f:
            # import
            f.write(import_template.format(package='.', statement='Base'))
            f.write(import_template.format(package='sqlalchemy.types',
                                           statement='Integer, String, Text, Boolean, PickleType, Date, Time, Unicode, BigInteger, Interval'))
                                            # 导入了所有常用类型以便直接添加表
            f.write(import_template.format(package='sqlalchemy', statement='Column, ForeignKey'))
            f.write('\n')
            for table in table_info:
                f.write(class_template.format(table_name=table['table_name']))
                f.write(tab + "__tablename__ = '{}'\n\n".format(table['table_name']))
                columns = self.render_column(table)
                for column in columns:
                    f.write(tab + column + '\n')
                f.write('\n')

        with open('models/__init__.py', 'a+') as f:
            init_s = 'from .{} import * # from module<{}>\n'.format(self.mapping_filename, self.current_mod.get('name'))
            f.write(init_s)
        # 数据库更新
        if update:
            self.update('module<{}> register table'.format(self.current_mod.get('name')))
        self.scan_file('models/{}.py'.format(self.mapping_filename)) # 重新扫描文件
        # file_pos and api_info
        for table in table_info:
            t = self.session.query(Tables).filter_by(name=table['table_name']).first()
            for i in self.file_info('models/{}.py'.format(self.mapping_filename))['classes']:
                # i: class_in_file对象 模型定义文件中的类
                if i.name == table['table_name']:
                    t.file_pos = i.to_json()
                    # 记录table_context
                    if current_app.__getattribute__('table_context'):
                        current_app.table_context.append(table_Context(i, table))
                    else:
                        current_app.table_context = [table_Context(i, table)]


        self.session.commit()
        current_app.api_manager.register_query_api()

    def check_table_info(self, table_info):
        """
        检查表信息正确性
        :param table_info:
        :return:
        """
        tables = []
        for table in table_info:
            # table name
            table_name = table.get('table_name', None)
            if table_name is None:
                raise ValueError('table_name is required')
            table['table_name'] = table_name.lower() # 表名统一小写
            # 表名查重
            if len(self.session.query(Tables).filter_by(name=table_name).all()):
                # 有同名表
                raise ValueError('table<{}> is existed'.format(table_name))
            if table_name in tables:
                # print(tables)
                raise ValueError('table_name<{}> is repeat'.format(table_name))
            if not formating_check(table_name):
                raise ValueError('table_name<{}> is invalid'.format(table_name))
            tables.append(table_name)
            # sensitivity
            try:
                int(table.get('sensitivity', 0))
            except ValueError:
                raise ValueError('sensitivity of table<{}> is invalid'.format(table_name))

        for table in table_info:
            table_name = table.get('table_name', None)
            primary_keys = table.get('primary_key', None)
            fields = table.get('fields', None)
            # primary key
            if primary_keys is None or len(primary_keys) == 0:
                raise ValueError('primary key of table<{}> is required', format(table_name))
            if fields is None:
                raise ValueError('fields of table<{}> is required', format(table_name))
            for primary_key in primary_keys:
                if primary_key not in fields:
                    raise ValueError('primary key<{}> is not defined'.format(primary_key))
            # fields
            # 该type列表支持python 其他语言支持后续添加
            types = ['Integer', 'String', 'Text', 'Boolean', 'PickleType',
                     'Date', 'Time', 'Unicode', 'BigInteger', 'Interval']
            for field in fields:
                if not formating_check(field):
                    raise ValueError('field name<{}> in table<{}> is invalid'.format(field, table_name))
                try:
                    int(fields[field].get('sensitivity', 0))
                except ValueError:
                    raise ValueError('sensitivity of field<{}> in table<{}> is invalid'.format(field, table_name))
                # type
                type = fields[field].get('type', None)
                if type is None:
                    raise ValueError('type of field<{}> is required', format(field))
                if type not in types:
                    raise ValueError('type<{}> of field<{}> in table<{}> is invalid'.format(type, field, table))
                # length
                length = fields[field].get('length', 256)
                if int(length) > 256:
                    raise ValueError('length of field<{}> in table<{}> is larger than 256.'.format(field, table))
                # foreignkey
                foreign_key = fields[field].get('foreignkey', None)
                if foreign_key is None:
                    continue
                if 'table' not in foreign_key and 'field' not in foreign_key:
                    continue
                if 'table' not in foreign_key and 'field' in foreign_key:
                    raise ValueError('foreign key of field<{}> in table<{}> requires param table'.format(field, table))
                if 'table' in foreign_key and 'field' not in foreign_key:
                    raise ValueError('foreign key of field<{}> in table<{}> requires param field'.format(field, table))

                if foreign_key['table'] not in tables:
                    print(foreign_key['table'])
                    raise ValueError('foreign table of field<{}> in table<{}> is not defined'.format(field, table['table_name']))

                for t in table_info:
                    if t['table_name'] == foreign_key['table']:
                        if foreign_key['field'] not in t['fields']:
                            raise ValueError(
                                'foreign key of field<{}> in table<{}> is not defined'.format(field, table))

    @staticmethod
    def render_column(table):
        """
        渲染列语句
        :param table:
        :return:
        """
        columns = []
        column_template = '{field} = Column({type}{params})'
        for field_ in table['fields']:
            param_s = ''
            # 生成type参数
            field_name = field_
            field = table['fields'][field_]  # 获取field字典
            type = field['type']
            if type == 'String':
                length = field.get('length', '256')
            else:
                length = ''
            if length != '':
                length = '({})'.format(length)

            type_s = '{name}{length}'.format(name=type, length=length)
            # foreign key
            foreignkey = field.get('foreignkey', None)
            if foreignkey is not None:
                param_s += ", ForeignKey('{table}.{field}')".format(table=foreignkey['table'],
                                                                    field=foreignkey['field'])
            # primary key
            if field_name in table['primary_key']:
                param_s += ', primary_key=True'
            # 生成其他参数 对default参数支持待开发
            nullable = field.get('nullable', True)
            unique = field.get('unique', False)

            if not nullable:
                param_s += ', nullable=False'
            if unique:
                param_s += ', unique=True'

            # 生成语句
            column_s = column_template.format(field=field_name, type=type_s, params=param_s)
            columns.append(column_s)
        return columns
    #---------------------------

    def delete_table(self, table_name, update=True):
        """
        删除表
        :param table_name:
        :param update:
        :return:
        """
        t = self.session.query(Tables).filter_by(name=table_name).first()
        if not t:
            raise ValueError('{0} is not defined'.format(table_name))
        # 修改映射文件
        if not t.file_pos:
            raise ValueError('file_pos is None')
        pos_info = json.loads(t.file_pos)
        temp_file_name = '{0}\\{1}.temp'.format(os.path.dirname(pos_info['file']),
                                             pos_info['file'].split('\\')[-1].split('.')[0])
        temp_f = open(temp_file_name, 'w', encoding='utf-8')
        with open(pos_info['file'], 'r', encoding='utf-8') as r_f:
            line_id = 0
            for line in r_f:
                line_id += 1
                if int(pos_info['begin']) <= line_id <= int(pos_info['end']):
                    line = ''
                temp_f.write(line)
        temp_f.close()
        os.remove(pos_info['file'])
        os.renames(temp_file_name, temp_file_name.replace('.temp', '.py'))
        # 更新Table表Field表
        table_row = self.session.query(Tables).filter_by(name=table_name, owner_id=self.current_mod.get('id')).first()
        for field in table_row.fields:
            self.session.delete(field)
        self.session.delete(table_row)
        self.session.commit()
        # 数据库更新
        if update:
            self.update('delete table {0}'.format(table_name))
        self.scan_file(pos_info['file'])  # 重新扫描文件

    def update(self, msg):
        """
        更新数据库
        :param msg:
        :return:
        """
        # 绝对路径需要写入路径
        alembic_cfg = Config('C:\\Users\\93214\\Documents\\projects\\python_proj\\IMS\\DataService\\alembic.ini')
        command.revision(alembic_cfg, msg, autogenerate=True)
        command.upgrade(alembic_cfg, 'head')

    def _test_teardown(self, table_info):
        """
        测试失败时恢复环境
        :param table_info:
        :return:
        """
        # 删除__init__导入
        with open('models/__init__.py', 'r') as r_f:
            w_f = open('models/__init__.temp', 'w')
            for line in r_f:
                if self.gene_filename() in line:
                    line = ''
                w_f.write(line)
            w_f.close()
        os.remove('models/__init__.py')
        os.renames('models/__init__.temp', 'models/__init__.py')
        # 删除映射文件
        os.remove('models/{}.py'.format(self.gene_filename()))
        # 删除Table Field表
        for table in table_info:
            table_row = self.session.query(Tables).filter_by(name=table['table_name'], owner_id=self.current_mod.get('id')).first()
            for field in table_row.fields:
                self.session.delete(field)
            self.session.delete(table_row)
        self.session.commit()
        # 恢复alembic版本
        alembic_cfg = Config('alembic.ini')
        command.downgrade(alembic_cfg, '26b27c9afbf9')

    def gene_filename(self):
        m = hashlib.md5()
        m.update(self.current_mod.get('name').encode())
        mapping_filename = m.hexdigest()
        if not formating_check(mapping_filename):
            mapping_filename = '_' + mapping_filename
        return mapping_filename

    #---------------------------

    def get_table_info(self, name):
        """
        返回相应模型的class_in_file对象
        需要生成的模型都来自sys部分, 暂时不考虑其他模块的生成
        :param name:
        :return:
        """
        classes_info = self.scan_file(os.path.abspath('models/sys.py'))['classes']
        for cl in classes_info:
            module = importlib.import_module('.models.{0}'.format(cl.file_name.split('.')[0]), 'DataService')
            if getattr(module, cl.name).__tablename__.lower() == name.lower():
                return cl
        return None



