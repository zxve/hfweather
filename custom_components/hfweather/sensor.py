"""Typed native sensor values with stable legacy unique IDs."""

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.const import (
    PERCENTAGE,
    UnitOfDensity,
    UnitOfLength,
    UnitOfPressure,
    UnitOfSpeed,
    UnitOfTemperature,
)

from .const import DOMAIN
from .entity import HfEntity


@dataclass(frozen=True, kw_only=True)
class HfSensorDescription(SensorEntityDescription):
    group: str = "current"


def description(
    key, name, unit=None, device_class=None, group="current", icon=None, enabled=True
):
    return HfSensorDescription(
        key=key,
        name=name,
        native_unit_of_measurement=unit,
        device_class=device_class,
        group=group,
        icon=icon,
        entity_registry_enabled_default=enabled,
    )


SENSORS = (
    description(
        "temperature",
        "室外温度",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
    ),
    description("humidity", "室外湿度", PERCENTAGE, SensorDeviceClass.HUMIDITY),
    description(
        "feelsLike",
        "体感温度",
        UnitOfTemperature.CELSIUS,
        SensorDeviceClass.TEMPERATURE,
    ),
    description("text", "天气描述", icon="mdi:weather-partly-cloudy"),
    description(
        "precip",
        "小时降水量",
        UnitOfLength.MILLIMETERS,
        SensorDeviceClass.PRECIPITATION,
    ),
    description("windDir", "风向", icon="mdi:windsock", enabled=False),
    description("windScale", "风力等级", icon="mdi:weather-windy"),
    description(
        "windSpeed",
        "风速",
        UnitOfSpeed.KILOMETERS_PER_HOUR,
        SensorDeviceClass.WIND_SPEED,
    ),
    description(
        "dew", "露点温度", UnitOfTemperature.CELSIUS, SensorDeviceClass.TEMPERATURE
    ),
    description("pressure", "大气压强", UnitOfPressure.HPA, SensorDeviceClass.PRESSURE),
    description("vis", "能见度", UnitOfLength.KILOMETERS, SensorDeviceClass.DISTANCE),
    # Keep this legacy entity, but do not fabricate a city name from fxLink.
    description("place", "气候位置", icon="mdi:map-marker"),
    description("cloud", "云量", PERCENTAGE),
    description("primary", "空气质量的主要污染物", group="air"),
    description("category", "空气质量指数级别", group="air"),
    description("level", "空气质量指数等级", group="air"),
    *(
        description(key, name, UnitOfDensity.MICROGRAMS_PER_CUBIC_METER, kind, "air")
        for key, name, kind in (
            ("pm25", "PM2.5", SensorDeviceClass.PM25),
            ("pm10", "PM10", SensorDeviceClass.PM10),
            ("no2", "二氧化氮", SensorDeviceClass.NITROGEN_DIOXIDE),
            ("so2", "二氧化硫", SensorDeviceClass.SULPHUR_DIOXIDE),
            ("o3", "臭氧", SensorDeviceClass.OZONE),
        )
    ),
    description(
        "co",
        "一氧化碳",
        UnitOfDensity.MILLIGRAMS_PER_CUBIC_METER,
        SensorDeviceClass.CO,
        "air",
    ),
    description(
        "qlty", "综合空气质量", device_class=SensorDeviceClass.AQI, group="air"
    ),
    description("disaster_warn", "灾害预警", group="warning", icon="mdi:alert"),
)
SUGGESTIONS = tuple(
    description(key, name, group="suggestions")
    for key, name in (
        ("air", "空气污染扩散条件指数"),
        ("comfortable", "舒适度指数"),
        ("cw", "洗车指数"),
        ("drsg", "穿衣指数"),
        ("flu", "感冒指数"),
        ("sport", "运动指数"),
        ("trav", "旅行指数"),
        ("uv", "紫外线指数"),
        ("guomin", "过敏指数"),
        ("kongtiao", "空调开启指数"),
        ("sunglass", "太阳镜指数"),
        ("fangshai", "防晒指数"),
        ("liangshai", "晾晒指数"),
        ("jiaotong", "交通指数"),
    )
)


async def async_setup_entry(hass, entry, async_add_entities):
    coordinator = hass.data[DOMAIN][entry.entry_id]
    definitions = SENSORS + (SUGGESTIONS if entry.options.get("sugg", False) else ())
    async_add_entities(
        HfweatherSensor(coordinator, definition) for definition in definitions
    )


class HfweatherSensor(HfEntity, SensorEntity):
    def __init__(self, coordinator, definition):
        super().__init__(coordinator, definition.group)
        self.entity_description = definition
        # Preserve legacy suffix spelling and naming during registry migration.
        self._attr_unique_id = f"{coordinator.entry.unique_id}-{definition.key}".lower()
        self._attr_name = f"{coordinator.entry.title} {definition.name}"

    @property
    def native_value(self):
        value = self.values.get(self.entity_description.key)
        if self._product_group == "suggestions" and isinstance(value, list):
            value = value[0] if value else None
        return value[:255] if isinstance(value, str) else value

    @property
    def available(self):
        return super().available and self.native_value is not None

    @property
    def extra_state_attributes(self):
        attrs = super().extra_state_attributes
        if self._product_group == "warning":
            attrs["warning_text"] = (self.values.get("disaster_warn") or "")[:8192]
            attrs["warning_count"] = self.values.get("warning_count")
        if self._product_group == "suggestions":
            value = self.values.get(self.entity_description.key)
            if isinstance(value, list) and len(value) > 1:
                attrs["suggestion"] = value[1]
        return attrs
