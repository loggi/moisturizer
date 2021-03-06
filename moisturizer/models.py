import datetime
import logging
import uuid

from cassandra.cqlengine import models, columns, management, usertype


NATIVE_JSONSCHEMA_TYPE_MAPPER = {
    bool: ('boolean', None),
    int: ('integer', None),
    float: ('number', None),
    str: ('string', None),
    dict: ('object', None),
    list: ('array', None),
}

JSONSCHEMA_CQL_TYPE_MAPPER = {
    ('string', None): columns.Text,
    ('number', None): columns.Decimal,
    ('integer', None): columns.BigInt,
    ('boolean', None): columns.Boolean,
    ('null', None): lambda **_: None,
    ('string', 'date-time'): columns.DateTime,
    ('string', 'uuid'): columns.UUID,
    ('number', 'float'): columns.Float,
    ('number', 'double'): columns.Double,
    ('object', 'descriptor'): lambda **kwargs:
        columns.Map(columns.Text(),
                    columns.UserDefinedType(DescriptorFieldType), **kwargs),
}


CQL_JSONSCHEMA_TYPE_MAPPER = {
    v: k for v, k in JSONSCHEMA_CQL_TYPE_MAPPER
}

DEFAULT_CQL_TYPE = columns.Text


logger = logging.getLogger("moisturizer.models")


DoesNotExist = models.BaseModel.DoesNotExist


class InferredModel(models.Model):
    """
    Abstract class for inferred type models.

    Creates a typed object with the ``infer_model()`` call or by
    extending and creating custom inferred models.
    """
    id = columns.Text(primary_key=True,
                      default=lambda: str(uuid.uuid1().hex))
    last_modified = columns.DateTime(index=True,
                                     default=datetime.datetime.now)

    @classmethod
    def add_column(cls, name, column_type):
        column_type.column_name = name
        descriptor = models.ColumnDescriptor(column_type)

        cls._defined_columns[name] = column_type
        cls._columns[name] = column_type
        setattr(cls, name, descriptor)

    @classmethod
    def from_descriptor(cls, descriptor):
        """
        Builds an InferredModel child class from a descriptor object.
        """

        Model = type(descriptor.id, (cls,), {})
        for name, field in descriptor.properties.items():

            # Ignore explicitly declared fields
            if not getattr(cls, name, None):
                Model.add_column(name, field.as_column())

        return Model


class DescriptorFieldType(usertype.UserType):
    type = columns.Text()
    format = columns.Text(default='')
    primary_key = columns.Boolean(default=False)
    partition_key = columns.Boolean(default=False)
    required = columns.Boolean(default=False)
    index = columns.Boolean(default=True, db_field='index_')

    @classmethod
    def from_value(cls, value):
        for native, field in NATIVE_JSONSCHEMA_TYPE_MAPPER.items():
            if isinstance(value, native):
                type_, format_ = field
                return cls(type=type_, format=format_ or '')

    def as_column(self):
        type_, format_ = self.type, self.format or None
        field = JSONSCHEMA_CQL_TYPE_MAPPER.get((type_, format_),
                                               DEFAULT_CQL_TYPE)
        return field(
            primary_key=self.primary_key,
            partition_key=self.partition_key,
            index=self.index,
            required=self.required,
            default=None,
        )


class DescriptorModel(InferredModel):
    properties = columns.Map(columns.Text,
                             columns.UserDefinedType(DescriptorFieldType))

    def __init__(self, *args,  **kwargs):
        super().__init__(*args, **kwargs)
        self.set_default_properties()

    @property
    def schema(self):
        return {k: v.as_column() for k, v in self.properties.items()}

    @property
    def model(self):
        return InferredModel.from_descriptor(self)

    def set_default_properties(self):
        self.properties.update(**{
            'id': DescriptorFieldType(type='string',
                                      format='',
                                      primary_key=True,
                                      partition_key=True),
            'last_modified': DescriptorFieldType(type='string',
                                                 format='date-time',
                                                 index=True),
        })

    def infer_schema_change(self, object_):
        new_fields = {k: DescriptorFieldType.from_value(v)
                      for k, v in object_.items() if k not in self.properties}

        if not new_fields:
            return

        self.properties.update(**new_fields)  # noqa
        self.save()

        logger.info('Mutating schema.', extra={
            'type_id': self.id,
        })

        management.sync_table(self.model)
        return new_fields

    @classmethod
    def create(cls, *args, **kwargs):
        created = cls(*args, **kwargs)
        created.set_default_properties()
        created.save()

        logger.info('Creating schema.', extra={
            'type_id': created.id,
        })

        management.sync_table(created.model)
        return created

    def save(self, **kwargs):
        logger.info('Updating schema.', extra={
            'type_id': self.id,
        })

        management.sync_table(self.model)
        return super().save(**kwargs)

    def delete(self, **kwargs):
        logger.info('Deleting schema.', extra={
            'type_id': self.id,
        })

        management.drop_table(self.model)
        return super().delete(**kwargs)
