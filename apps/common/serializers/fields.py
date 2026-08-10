# -*- coding: utf-8 -*-
#
import phonenumbers
from django.core.exceptions import ObjectDoesNotExist
from django.db.models import Model
from django.utils.translation import gettext_lazy as _
from rest_framework import serializers
from rest_framework.fields import empty

from common.db.fields import TreeChoices, JSONManyToManyField as ModelJSONManyToManyField
from common.utils import decrypt_session_password, is_uuid

__all__ = [
    "ReadableHiddenField",
    "EncryptedField",
    "LabeledChoiceField",
    "ObjectRelatedField",
    "BitChoicesField",
    "TreeChoicesField",
    "LabeledMultipleChoiceField",
    "PhoneField",
    "JSONManyToManyField",
    "LabelRelatedField",
]


# ReadableHiddenField
# -------------------


class ReadableHiddenField(serializers.HiddenField):
    """A readable HiddenField"""

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.write_only = False

    def to_representation(self, value):
        if hasattr(value, "id"):
            return getattr(value, "id")
        return value


class EncryptedField(serializers.CharField):
    def __init__(self, write_only=None, **kwargs):
        if write_only is None:
            write_only = True
        kwargs["write_only"] = write_only
        super().__init__(**kwargs)

    def to_internal_value(self, value):
        value = super().to_internal_value(value)
        return decrypt_session_password(value)


class LabeledChoiceField(serializers.ChoiceField):
    def to_representation(self, key):
        if key is None:
            return key
        label = self.choices.get(key, key)
        return {"value": key, "label": label}

    def to_internal_value(self, data):
        if isinstance(data, dict):
            data = data.get("value")

        if isinstance(data, str) and "(" in data and data.endswith(")"):
            data = data.strip(")").split('(')[-1]
        return super(LabeledChoiceField, self).to_internal_value(data)


class LabeledMultipleChoiceField(serializers.MultipleChoiceField):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.choice_mapper = {
            key: value for key, value in self.choices.items()
        }

    def to_representation(self, keys):
        if keys is None:
            return keys
        return [
            {"value": key, "label": self.choice_mapper.get(key)}
            for key in keys
        ]

    def to_internal_value(self, data):
        if not data:
            return data

        if isinstance(data[0], dict):
            return [item.get("value") for item in data]
        else:
            return data


class LabelRelatedField(serializers.RelatedField):
    def __init__(self, **kwargs):
        queryset = kwargs.pop("queryset", None)
        if queryset is None:
            from labels.models import LabeledResource
            queryset = LabeledResource.objects.all()

        kwargs = {**kwargs}
        read_only = kwargs.get("read_only", False)
        if not read_only:
            kwargs["queryset"] = queryset
        super().__init__(**kwargs)

    def to_file_representation(self, value):
        if value is None:
            return value
        return "{}:{}".format(value.get('name'), value.get('value'))

    def to_representation(self, value):
        if value is None:
            return value
        label = value.label
        if not label:
            return None
        return {'id': label.id, 'name': label.name, 'value': label.value, 'color': label.color}

    def to_internal_value(self, data):
        from labels.models import LabeledResource, Label
        if data is None:
            return data
        if isinstance(data, dict) and (data.get("id") or data.get("pk")):
            pk = data.get("id") or data.get("pk")
            label = Label.objects.get(pk=pk)
        elif is_uuid(data):
            label = Label.objects.get(pk=data)
        else:
            if isinstance(data, dict):
                k = data.get("name")
                v = data.get("value")
            elif isinstance(data, str) and ":" in data:
                k, v = [x.strip() for x in data.split(":", 1)]
            else:
                raise serializers.ValidationError(_("Invalid data type"))
            from labels.serializers import LabelSerializer

            validated = LabelSerializer.validate_name_value(k, v)
            label, __ = Label.objects.get_or_create(
                name=validated['name'],
                value=validated['value'],
                defaults=validated,
            )
        return LabeledResource(label=label)


class ObjectRelatedField(serializers.RelatedField):
    default_error_messages = {
        "required": _("This field is required."),
        "does_not_exist": _('Invalid pk "{pk_value}" - object does not exist.'),
        "incorrect_type": _("Incorrect type. Expected pk value, received {data_type}."),
    }

    def __init__(self, **kwargs):
        self.attrs = kwargs.pop("attrs", None) or ("id", "name")
        self.many = kwargs.get("many", False)
        super().__init__(**kwargs)

    def to_representation(self, value):
        data = {}
        for attr in self.attrs:
            if not hasattr(value, attr):
                continue
            data[attr] = getattr(value, attr)
        return data

    def to_internal_value(self, data):
        queryset = self.get_queryset()
        if isinstance(data, Model):
            return queryset.get(pk=data.pk)

        if not isinstance(data, dict):
            pk = data
        else:
            pk = data.get("id") or data.get("pk") or data.get(self.attrs[0])

        try:
            if isinstance(data, bool):
                raise TypeError
            return queryset.get(pk=pk)
        except ObjectDoesNotExist:
            self.fail("does_not_exist", pk_value=pk)
        except (TypeError, ValueError):
            self.fail("incorrect_type", data_type=type(pk).__name__)

    def get_schema(self):
        """
        Provide an OpenAPI schema for drf-spectacular
        """
        # Get the field's basic information
        field_type = 'array' if self.many else 'object'

        if field_type == 'array':
            # If it's a many-to-many relation
            return {
                'type': 'array',
                'items': self._get_openapi_item_schema(),
                'description': getattr(self, 'help_text', ''),
                'title': getattr(self, 'label', ''),
            }
        else:
            # If it's a one-to-one relation
            return {
                'type': 'object',
                'properties': self._get_openapi_properties_schema(),
                'description': getattr(self, 'help_text', ''),
                'title': getattr(self, 'label', ''),
            }

    def _get_openapi_item_schema(self):
        """
        Get the OpenAPI schema for an array item
        """
        return self._get_openapi_object_schema()

    def _get_openapi_object_schema(self):
        """
        Get the OpenAPI schema for an object
        """
        properties = {}

        # Dynamically analyze the attribute types in attrs
        for attr in self.attrs:
            # Try to get field information from the queryset's model
            field_type = self._infer_field_type(attr)
            properties[attr] = {
                'type': field_type,
                'description': f'{attr} field'
            }

        return {
            'type': 'object',
            'properties': properties,
            'required': ['id'] if 'id' in self.attrs else []
        }

    def _infer_field_type(self, attr_name):
        """
        Intelligently infer the field type
        """
        try:
            # If there is a queryset, try to get field information from the model
            if hasattr(self, 'queryset') and self.queryset is not None:
                model = self.queryset.model
                if hasattr(model, '_meta') and hasattr(model._meta, 'fields'):
                    field = model._meta.get_field(attr_name)
                    if field:
                        return self._map_django_field_type(field)
        except Exception:
            pass

        # If there is no queryset or field information can't be obtained, use heuristic rules
        return self._heuristic_field_type(attr_name)

    def _map_django_field_type(self, field):
        """
        Map a Django field type to an OpenAPI type
        """
        field_type = type(field).__name__

        # Integer types
        if 'Integer' in field_type or 'BigInteger' in field_type or 'SmallInteger' in field_type:
            return 'integer'
        # Floating-point types
        elif 'Float' in field_type or 'Decimal' in field_type:
            return 'number'
        # Boolean type
        elif 'Boolean' in field_type:
            return 'boolean'
        # Date/time types
        elif 'DateTime' in field_type or 'Date' in field_type or 'Time' in field_type:
            return 'string'
        # File types
        elif 'File' in field_type or 'Image' in field_type:
            return 'string'
        # Other types default to string
        else:
            return 'string'

    def _heuristic_field_type(self, attr_name):
        """
        Heuristically infer the field type
        """
        # Heuristic rules based on the attribute name

        if attr_name in ['is_active', 'enabled', 'visible'] or attr_name.startswith('is_'):
            return 'boolean'
        elif attr_name in ['count', 'number', 'size', 'amount']:
            return 'integer'
        elif attr_name in ['price', 'rate', 'percentage']:
            return 'number'
        else:
            # Default to returning the string type
            return 'string'

    def _get_openapi_properties_schema(self):
        """
        Get the OpenAPI schema for object properties
        """
        return self._get_openapi_object_schema()['properties']


class TreeChoicesField(serializers.MultipleChoiceField):
    def __init__(self, choice_cls, **kwargs):
        assert issubclass(choice_cls, TreeChoices)
        choices = [(c.name, c.label) for c in choice_cls]
        self.tree = choice_cls.tree()
        self._choice_cls = choice_cls
        super().__init__(choices=choices, **kwargs)

    def to_internal_value(self, data):
        if not data:
            return data
        if isinstance(data[0], dict):
            return [item.get("value") for item in data]
        else:
            return data


class BitChoicesField(TreeChoicesField):
    """
    Bit field
    """

    def to_representation(self, value):
        if isinstance(value, list) and len(value) == 1:
            # Swagger will iterate using field.choices.keys() and pass it in
            return [
                {"value": c.name, "label": c.label}
                for c in self._choice_cls
                if c.name == value[0]
            ]
        return [
            {"value": c.name, "label": c.label}
            for c in self._choice_cls
            if c.value & value == c.value
        ]

    def to_internal_value(self, data):
        if not isinstance(data, list):
            raise serializers.ValidationError(_("Invalid data type, should be list"))
        value = 0
        if not data:
            return value
        if isinstance(data[0], dict):
            data = [d["value"] for d in data]
        # All of them
        if "all" in data:
            for c in self._choice_cls:
                value |= c.value
            return value

        name_value_map = {c.name: c.value for c in self._choice_cls}
        for name in data:
            if name not in name_value_map:
                raise serializers.ValidationError(_("Invalid choice: {}").format(name))
            value |= name_value_map[name]
        return value

    def get_schema(self):
        """
        Provide an OpenAPI schema for drf-spectacular
        """
        return {
            'type': 'array',
            'items': {
                'type': 'object',
                'properties': {
                    'value': {'type': 'string'},
                    'label': {'type': 'string'}
                }
            },
            'description': getattr(self, 'help_text', ''),
            'title': getattr(self, 'label', ''),
        }

    def run_validation(self, data=empty):
        """
        Note:
        When creating an authorization rule without an actions field, the default value
        (set in AssetPermission) is used, and fields like ['connect', '...'] end up being
        saved to the database as-is, causing a type error.
        Here the obtained value is run through to_internal_value again to convert it to the internal value
        """
        data = super().run_validation(data)
        if isinstance(data, int):
            return data
        value = self.to_internal_value(data)
        self.run_validators(value)
        return value


class PhoneField(serializers.CharField):

    def to_internal_value(self, data):
        if isinstance(data, dict):
            code = data.get('code')
            phone = data.get('phone', '')
            if code and phone:
                code = code.replace('+', '')
                data = '+{}{}'.format(code, phone)
            else:
                data = phone
        if data:
            try:
                phone = phonenumbers.parse(data, 'CN')
                data = '+{}{}'.format(phone.country_code, phone.national_number)
            except phonenumbers.NumberParseException:
                data = '+86{}'.format(data)

        return super().to_internal_value(data)

    def to_representation(self, value):
        try:
            phone = phonenumbers.parse(value, 'CN')
            value = {'code': '+%s' % phone.country_code, 'phone': str(phone.national_number)}
        except phonenumbers.NumberParseException:
            value = {'code': '+86', 'phone': value}
        return value


class JSONManyToManyField(serializers.JSONField):
    def to_representation(self, manager):
        if manager is None:
            return manager
        value = manager.value
        if not isinstance(value, dict):
            return {"type": "ids", "ids": []}
        if value.get("type") == "ids":
            valid_ids = manager.all().values_list("id", flat=True)
            valid_ids = [str(i) for i in valid_ids]
            return {"type": "ids", "ids": valid_ids}
        return value

    def to_internal_value(self, data):
        if not data:
            data = {}
        try:
            data = super().to_internal_value(data)
            ModelJSONManyToManyField.check_value(data)
        except ValueError as e:
            raise serializers.ValidationError(e)
        return super().to_internal_value(data)
