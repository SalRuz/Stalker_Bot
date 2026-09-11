import asyncio
import datetime
import enum
import json
import logging
from typing import Any, Dict, List, Optional

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from sqlalchemy import (
    BIGINT,
    BOOLEAN,
    DATETIME,
    FLOAT,
    INTEGER,
    JSON,
    STRING,
    TEXT,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum as SQLEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# =====================================================================
# 1. СИСТЕМНАЯ КОНФИГУРАЦИЯ И ЛОГИРОВАНИЕ
# =====================================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
)
logger = logging.getLogger("STALKER_MMO")

BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"
DATABASE_URL = "sqlite+aiosqlite:///stalker_zone.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


# =====================================================================
# 2. ПЕРЕЧИСЛЕНИЯ (ENUMS) ДЛЯ ИГРОВОЙ МЕХАНИКИ
# =====================================================================

class FactionType(str, enum.Enum):
    LONER = "Одиночки"
    BANDIT = "Бандиты"
    DUTY = "Долг"
    FREEDOM = "Свобода"
    MONOLITH = "Монолит"
    MILITARY = "Военные"
    MERCENARY = "Наемники"
    ECOLOGIST = "Ученые"


class ItemType(str, enum.Enum):
    WEAPON = "Оружие"
    ARMOR = "Броня"
    AMMO = "Патроны"
    MEDICINE = "Медикаменты"
    FOOD = "Еда"
    ARTIFACT = "Артефакт"
    DETECTOR = "Детектор"
    JUNK = "Хлам"


class ItemRarity(str, enum.Enum):
    COMMON = "Обычный"
    UNCOMMON = "Необычный"
    RARE = "Редкий"
    EPIC = "Эпический"
    LEGENDARY = "Легендарный"


class QuestStatus(str, enum.Enum):
    ACTIVE = "Активен"
    COMPLETED = "Завершен"
    FAILED = "Провален"


class CombatState(str, enum.Enum):
    IDLE = "Вне боя"
    SEARCHING = "Поиск цели"
    IN_COMBAT = "В бою"


# =====================================================================
# 3. SHEMA СХЕМЫ БАЗЫ ДАННЫХ (ORM MODELS)
# =====================================================================

class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, index=True) # Telegram User ID
    username: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    full_name: Mapped[str] = mapped_column(String(128), default="Сталкер")
    
    # Характеристики персонажа
    health: Mapped[float] = mapped_column(Float, default=100.0)
    max_health: Mapped[float] = mapped_column(Float, default=100.0)
    radiation: Mapped[float] = mapped_column(Float, default=0.0) # В Зивертах (мкЗв)
    hunger: Mapped[float] = mapped_column(Float, default=0.0)    # 0..100%
    stamina: Mapped[float] = mapped_column(Float, default=100.0)
    
    # Экономика и опыт
    money: Mapped[int] = mapped_column(BigInteger, default=1000)
    experience: Mapped[int] = mapped_column(BigInteger, default=0)
    level: Mapped[int] = mapped_column(Integer, default=1)
    
    # Фракция и локация
    faction: Mapped[FactionType] = mapped_column(SQLEnum(FactionType), default=FactionType.LONER)
    current_location_id: Mapped[str] = mapped_column(String(64), default="kordon_bunker")
    
    # Статистика
    pvp_kills: Mapped[int] = mapped_column(Integer, default=0)
    pve_kills: Mapped[int] = mapped_column(Integer, default=0)
    deaths: Mapped[int] = mapped_column(Integer, default=0)
    artifacts_found: Mapped[int] = mapped_column(Integer, default=0)
    
    # Состояние
    combat_state: Mapped[CombatState] = mapped_column(SQLEnum(CombatState), default=CombatState.IDLE)
    active_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True) # ID группы, где сейчас ведется бой
    
    # Экипированное снаряжение (ID предмтов из таблицы inventory_items)
    equipped_weapon_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    equipped_armor_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    equipped_detector_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    # Временные метки
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    last_action: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    # Связи
    inventory: Mapped[List["InventoryItem"]] = relationship("InventoryItem", back_populates="owner", cascade="all, delete-orphan")
    quests: Mapped[List["UserQuest"]] = relationship("UserQuest", back_populates="user", cascade="all, delete-orphan")
    clan_membership: Mapped[Optional["ClanMember"]] = relationship("ClanMember", back_populates="user", uselist=False)


class GroupChat(Base):
    __tablename__ = "group_chats"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True) # Telegram Chat ID
    title: Mapped[str] = mapped_column(String(255))
    chat_type: Mapped[str] = mapped_column(String(32)) # group / supergroup
    
    # Контроль территории и локация чата
    assigned_location_id: Mapped[str] = mapped_column(String(64), default="kordon_attract")
    controlling_faction: Mapped[Optional[FactionType]] = mapped_column(SQLEnum(FactionType), nullable=True)
    defense_level: Mapped[int] = mapped_column(Integer, default=0)
    
    # Настройки чата
    is_safe_zone: Mapped[bool] = mapped_column(Boolean, default=False)
    allow_pvp: Mapped[bool] = mapped_column(Boolean, default=True)
    last_emission_time: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)
    
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class Clan(Base):
    __tablename__ = "clans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    tag: Mapped[str] = mapped_column(String(10), unique=True)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    
    leader_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    faction: Mapped[FactionType] = mapped_column(SQLEnum(FactionType), default=FactionType.LONER)
    
    bank_money: Mapped[int] = mapped_column(BigInteger, default=0)
    rating_points: Mapped[int] = mapped_column(Integer, default=0)
    max_members: Mapped[int] = mapped_column(Integer, default=10)
    
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    members: Mapped[List["ClanMember"]] = relationship("ClanMember", back_populates="clan", cascade="all, delete-orphan")


class ClanMember(Base):
    __tablename__ = "clan_members"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    clan_id: Mapped[int] = mapped_column(Integer, ForeignKey("clans.id"))
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), unique=True)
    
    role: Mapped[str] = mapped_column(String(32), default="Рядовой") # Лидер, Зам, Боец, Рядовой
    joined_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    clan: Mapped["Clan"] = relationship("Clan", back_populates="members")
    user: Mapped["User"] = relationship("User", back_populates="clan_membership")


class ItemTemplate(Base):
    __tablename__ = "item_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True) # e.g. 'weapon_ak74', 'art_medusa'
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    item_type: Mapped[ItemType] = mapped_column(SQLEnum(ItemType))
    rarity: Mapped[ItemRarity] = mapped_column(SQLEnum(ItemRarity), default=ItemRarity.COMMON)
    
    base_price: Mapped[int] = mapped_column(Integer, default=100)
    weight: Mapped[float] = mapped_column(Float, default=0.5)
    
    # Характеристики в JSON (урон, защита, радиоактивность, лечение и т.д.)
    stats_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default={})


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    template_id: Mapped[str] = mapped_column(String(64), ForeignKey("item_templates.id"))
    
    durability: Mapped[float] = mapped_column(Float, default=100.0) # Прочность 0..100%
    quantity: Mapped[int] = mapped_column(Integer, default=1)
    is_equipped: Mapped[bool] = mapped_column(Boolean, default=False)
    
    acquired_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())

    owner: Mapped["User"] = relationship("User", back_populates="inventory")
    template: Mapped["ItemTemplate"] = relationship("ItemTemplate")


class AuctionLot(Base):
    __tablename__ = "auction_lots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    seller_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    item_id: Mapped[int] = mapped_column(Integer, ForeignKey("inventory_items.id"))
    
    price: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class QuestTemplate(Base):
    __tablename__ = "quest_templates"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(128))
    description: Mapped[Text] = mapped_column(Text)
    
    reward_money: Mapped[int] = mapped_column(Integer, default=0)
    reward_exp: Mapped[int] = mapped_column(Integer, default=0)
    reward_item_template_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    required_level: Mapped[int] = mapped_column(Integer, default=1)
    target_data_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default={}) # Кого убить / что принести


class UserQuest(Base):
    __tablename__ = "user_quests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"), index=True)
    quest_template_id: Mapped[str] = mapped_column(String(64), ForeignKey("quest_templates.id"))
    
    status: Mapped[QuestStatus] = mapped_column(SQLEnum(QuestStatus), default=QuestStatus.ACTIVE)
    progress_json: Mapped[Dict[str, Any]] = mapped_column(JSON, default={})
    
    started_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[Optional[datetime.datetime]] = mapped_column(DateTime, nullable=True)

    user: Mapped["User"] = relationship("User", back_populates="quests")
    template: Mapped["QuestTemplate"] = relationship("QuestTemplate")


class AnomalyField(Base):
    __tablename__ = "anomaly_fields"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    location_id: Mapped[str] = mapped_column(String(64), index=True)
    chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    
    name: Mapped[str] = mapped_column(String(64)) # «Жарка», «Воронки», «Карусель»
    hazard_level: Mapped[float] = mapped_column(Float, default=1.0)
    has_artifact: Mapped[bool] = mapped_column(Boolean, default=True)
    artifact_template_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    
    last_spawn: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


class CombatSession(Base):
    __tablename__ = "combat_sessions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, index=True) # ЛС или ID группы
    
    player_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id"))
    enemy_name: Mapped[str] = mapped_column(String(128))
    enemy_hp: Mapped[float] = mapped_column(Float)
    enemy_max_hp: Mapped[float] = mapped_column(Float)
    enemy_damage: Mapped[float] = mapped_column(Float)
    
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    log_json: Mapped[List[str]] = mapped_column(JSON, default=[])
    
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime, server_default=func.now())


# Инициализация таблиц БД
async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("База данных и все таблицы SQLAlchemy успешно инициализированы.")
# =====================================================================
# 4. АСИНХРОННЫЙ СЛОЙ CRUD-ЗАПРОСОВ И ИГРОВАЯ ЛОГИКА (SERVICE LAYER)
# =====================================================================

class PlayerService:
    """Сервис управления профилем, характеристиками и состоянием игрока."""

    @staticmethod
    async def get_or_create_player(
        session: AsyncSession,
        user_id: int,
        username: Optional[str] = None,
        full_name: str = "Сталкер"
    ) -> User:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        player = result.scalar_one_or_none()

        if not player:
            player = User(
                id=user_id,
                username=username,
                full_name=full_name,
                health=100.0,
                max_health=100.0,
                radiation=0.0,
                hunger=0.0,
                stamina=100.0,
                money=1000,
                experience=0,
                level=1,
                faction=FactionType.LONER,
                current_location_id="kordon_bunker"
            )
            session.add(player)
            await session.commit()
            await session.refresh(player)
            logger.info(f"Создан новый игрок [ID: {user_id}] {full_name}")
        else:
            # Обновляем никнейм если изменился
            if username and player.username != username:
                player.username = username
            if full_name and player.full_name != full_name:
                player.full_name = full_name
            await session.commit()

        return player

    @staticmethod
    async def get_player_full_profile(session: AsyncSession, user_id: int) -> Optional[User]:
        stmt = select(User).where(User.id == user_id)
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def modify_health(session: AsyncSession, player: User, amount: float) -> float:
        player.health = max(0.0, min(player.max_health, player.health + amount))
        if player.health <= 0:
            player.deaths += 1
            player.combat_state = CombatState.IDLE
            player.radiation = 0.0
            player.hunger = 0.0
            player.health = player.max_health * 0.5  # Респаун с 50% HP
            player.current_location_id = "kordon_bunker"  # Возврат на батины нары
            logger.info(f"Игрок [ID: {player.id}] погиб и респавнился на Кордоне.")
        await session.commit()
        return player.health

    @staticmethod
    async def apply_radiation(session: AsyncSession, player: User, amount: float) -> float:
        player.radiation = max(0.0, player.radiation + amount)
        if player.radiation > 100.0:  # Смертельная доза
            await PlayerService.modify_health(session, player, -999.0)
        await session.commit()
        return player.radiation

    @staticmethod
    async def add_experience(session: AsyncSession, player: User, exp_gained: int) -> bool:
        player.experience += exp_gained
        needed_exp = player.level * 500
        leveled_up = False

        while player.experience >= needed_exp:
            player.level += 1
            player.experience -= needed_exp
            player.max_health += 10.0
            player.health = player.max_health
            needed_exp = player.level * 500
            leveled_up = True
            logger.info(f"Игрок [ID: {player.id}] получил {player.level} уровень!")

        await session.commit()
        return leveled_up

    @staticmethod
    async def update_location(session: AsyncSession, player: User, location_id: str) -> None:
        player.current_location_id = location_id
        player.last_action = datetime.datetime.now(datetime.timezone.utc)
        await session.commit()


class InventoryService:
    """Сервис управления предметами, экипировкой и инвентарем."""

    @staticmethod
    async def add_item_to_inventory(
        session: AsyncSession,
        user_id: int,
        template_id: str,
        quantity: int = 1,
        durability: float = 100.0
    ) -> Optional[InventoryItem]:
        # Проверяем существование шаблона
        stmt = select(ItemTemplate).where(ItemTemplate.id == template_id)
        res = await session.execute(stmt)
        template = res.scalar_one_or_none()
        if not template:
            logger.error(f"Шаблон предмета '{template_id}' не найден!")
            return None

        # Стакаемые предметы (патроны, медикаменты, еда)
        if template.item_type in [ItemType.AMMO, ItemType.MEDICINE, ItemType.FOOD, ItemType.JUNK]:
            stmt_inv = select(InventoryItem).where(
                InventoryItem.user_id == user_id,
                InventoryItem.template_id == template_id
            )
            inv_res = await session.execute(stmt_inv)
            existing_item = inv_res.scalar_one_or_none()

            if existing_item:
                existing_item.quantity += quantity
                await session.commit()
                return existing_item

        new_item = InventoryItem(
            user_id=user_id,
            template_id=template_id,
            quantity=quantity,
            durability=durability,
            is_equipped=False
        )
        session.add(new_item)
        await session.commit()
        await session.refresh(new_item)
        return new_item

    @staticmethod
    async def remove_item_from_inventory(
        session: AsyncSession,
        item_id: int,
        quantity: int = 1
    ) -> bool:
        stmt = select(InventoryItem).where(InventoryItem.id == item_id)
        res = await session.execute(stmt)
        item = res.scalar_one_or_none()

        if not item:
            return False

        if item.quantity > quantity:
            item.quantity -= quantity
        else:
            # Если сломан или списан весь стек
            if item.is_equipped:
                # Снимаем из экипированного у игрока
                user_stmt = select(User).where(User.id == item.user_id)
                u_res = await session.execute(user_stmt)
                user = u_res.scalar_one_or_none()
                if user:
                    if user.equipped_weapon_id == item.id:
                        user.equipped_weapon_id = None
                    elif user.equipped_armor_id == item.id:
                        user.equipped_armor_id = None
                    elif user.equipped_detector_id == item.id:
                        user.equipped_detector_id = None
            await session.delete(item)

        await session.commit()
        return True

    @staticmethod
    async def equip_item(session: AsyncSession, user: User, item_id: int) -> bool:
        stmt = select(InventoryItem).where(
            InventoryItem.id == item_id,
            InventoryItem.user_id == user.id
        )
        res = await session.execute(stmt)
        item = res.scalar_one_or_none()

        if not item or item.is_equipped:
            return False

        # Получаем шаблон
        template_stmt = select(ItemTemplate).where(ItemTemplate.id == item.template_id)
        t_res = await session.execute(template_stmt)
        template = t_res.scalar_one_or_none()

        if not template:
            return False

        # Снимаем старый предмет аналогичного типа
        if template.item_type == ItemType.WEAPON:
            if user.equipped_weapon_id:
                await InventoryService._unequip_by_id(session, user.equipped_weapon_id)
            user.equipped_weapon_id = item.id
        elif template.item_type == ItemType.ARMOR:
            if user.equipped_armor_id:
                await InventoryService._unequip_by_id(session, user.equipped_armor_id)
            user.equipped_armor_id = item.id
        elif template.item_type == ItemType.DETECTOR:
            if user.equipped_detector_id:
                await InventoryService._unequip_by_id(session, user.equipped_detector_id)
            user.equipped_detector_id = item.id
        else:
            return False  # Предмет нельзя экипировать

        item.is_equipped = True
        await session.commit()
        return True

    @staticmethod
    async def _unequip_by_id(session: AsyncSession, item_id: int):
        stmt = select(InventoryItem).where(InventoryItem.id == item_id)
        res = await session.execute(stmt)
        item = res.scalar_one_or_none()
        if item:
            item.is_equipped = False

    @staticmethod
    async def get_player_inventory(session: AsyncSession, user_id: int) -> List[InventoryItem]:
        stmt = select(InventoryItem).where(InventoryItem.user_id == user_id)
        res = await session.execute(stmt)
        return list(res.scalars().all())


class GroupChatService:
    """Сервис работы с групповыми чатами и локациями зон контроля."""

    @staticmethod
    async def get_or_create_chat(
        session: AsyncSession,
        chat_id: int,
        title: str,
        chat_type: str
    ) -> GroupChat:
        stmt = select(GroupChat).where(GroupChat.id == chat_id)
        res = await session.execute(stmt)
        chat = res.scalar_one_or_none()

        if not chat:
            chat = GroupChat(
                id=chat_id,
                title=title,
                chat_type=chat_type,
                assigned_location_id="kordon_attract",
                is_safe_zone=False,
                allow_pvp=True
            )
            session.add(chat)
            await session.commit()
            await session.refresh(chat)
            logger.info(f"Зарегистрирован новый групповой чат: '{title}' [{chat_id}]")
        return chat

    @staticmethod
    async def update_faction_control(
        session: AsyncSession,
        chat_id: int,
        faction: FactionType,
        defense_points: int
    ) -> None:
        stmt = select(GroupChat).where(GroupChat.id == chat_id)
        res = await session.execute(stmt)
        chat = res.scalar_one_or_none()

        if chat:
            chat.controlling_faction = faction
            chat.defense_level = defense_points
            await session.commit()


class CombatService:
    """Менеджер боевых сессий (PvE / PvP)."""

    @staticmethod
    async def start_pve_session(
        session: AsyncSession,
        chat_id: int,
        player_id: int,
        enemy_name: str,
        enemy_hp: float,
        enemy_damage: float
    ) -> CombatSession:
        # Закрываем предыдущие неактивные сессии
        stmt_old = select(CombatSession).where(
            CombatSession.player_id == player_id,
            CombatSession.is_active == True
        )
        old_res = await session.execute(stmt_old)
        for old_s in old_res.scalars().all():
            old_s.is_active = False

        c_session = CombatSession(
            chat_id=chat_id,
            player_id=player_id,
            enemy_name=enemy_name,
            enemy_hp=enemy_hp,
            enemy_max_hp=enemy_hp,
            enemy_damage=enemy_damage,
            is_active=True,
            log_json=[f"⚔️ Начался бой с **{enemy_name}**!"]
        )
        session.add(c_session)

        # Обновляем статус игрока
        stmt_user = select(User).where(User.id == player_id)
        u_res = await session.execute(stmt_user)
        user = u_res.scalar_one_or_none()
        if user:
            user.combat_state = CombatState.IN_COMBAT
            user.active_chat_id = chat_id

        await session.commit()
        await session.refresh(c_session)
        return c_session

    @staticmethod
    async def get_active_session(session: AsyncSession, player_id: int) -> Optional[CombatSession]:
        stmt = select(CombatSession).where(
            CombatSession.player_id == player_id,
            CombatSession.is_active == True
        )
        res = await session.execute(stmt)
        return res.scalar_one_or_none()

    @staticmethod
    async def process_pve_turn(
        session: AsyncSession,
        combat_s: CombatSession,
        player: User,
        player_damage: float
    ) -> Dict[str, Any]:
        logs = list(combat_s.log_json)
        
        # Ход игрока
        combat_s.enemy_hp -= player_damage
        logs.append(f"💥 Вы нанесли {player_damage:.1f} урона [{combat_s.enemy_name}].")

        # Проверка победы
        if combat_s.enemy_hp <= 0:
            combat_s.enemy_hp = 0
            combat_s.is_active = False
            player.combat_state = CombatState.IDLE
            player.pve_kills += 1
            logs.append(f"🏆 Вы уничтожили **{combat_s.enemy_name}**!")
            combat_s.log_json = logs
            await session.commit()
            return {"status": "win", "logs": logs}

        # Ответный ход врага
        enemy_dmg = round(random.uniform(combat_s.enemy_damage * 0.7, combat_s.enemy_damage * 1.3), 1)
        player.health = max(0.0, player.health - enemy_dmg)
        logs.append(f"🩸 {combat_s.enemy_name} атаковал вас на {enemy_dmg:.1f} урона!")

        # Проверка поражения
        if player.health <= 0:
            combat_s.is_active = False
            player.combat_state = CombatState.IDLE
            player.deaths += 1
            player.health = player.max_health * 0.5
            player.current_location_id = "kordon_bunker"
            logs.append("☠️ Вы погибли в бою...")
            combat_s.log_json = logs
            await session.commit()
            return {"status": "defeat", "logs": logs}

        combat_s.log_json = logs
        await session.commit()
        return {"status": "ongoing", "logs": logs}
# =====================================================================
# 5. МИДДЛВАРИ (MIDDLEWARES) И ВСПОМОГАТЕЛЬНЫЕ КОМПОНЕНТЫ
# =====================================================================

import time
from typing import Any, Awaitable, Callable, Dict
from aiogram.dispatcher.middlewares.base import BaseMiddleware
from aiogram.types import TelegramObject


class DatabaseMiddleware(BaseMiddleware):
    """Миддлварь для автоинжекции сессии SQLAlchemy в хэндлеры."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        async with AsyncSessionLocal() as session:
            data["session"] = session
            return await handler(event, data)


class UserRegistrationMiddleware(BaseMiddleware):
    """Миддлварь автоматической регистрации пользователей и групповых чатов."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        session: AsyncSession = data.get("session")
        user = data.get("event_from_user")
        chat = data.get("event_chat")

        if user and session and not user.is_bot:
            db_user = await PlayerService.get_or_create_player(
                session=session,
                user_id=user.id,
                username=user.username,
                full_name=user.full_name
            )
            data["db_user"] = db_user

        if chat and session and chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
            db_chat = await GroupChatService.get_or_create_chat(
                session=session,
                chat_id=chat.id,
                title=chat.title or "Групповой чат",
                chat_type=chat.type
            )
            data["db_chat"] = db_chat

        return await handler(event, data)


class ThrottlingMiddleware(BaseMiddleware):
    """Anti-flood миддлварь для защиты от спама командами."""

    def __init__(self, rate_limit: float = 0.8):
        self.rate_limit = rate_limit
        self.user_timestamps: Dict[int, float] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any]
    ) -> Any:
        user = data.get("event_from_user")
        if user:
            now = time.time()
            last_time = self.user_timestamps.get(user.id, 0)
            if now - last_time < self.rate_limit:
                if isinstance(event, CallbackQuery):
                    await event.answer("⚠️ Не так быстро! Сталкеры не суетятся.", show_alert=False)
                return
            self.user_timestamps[user.id] = now
        return await handler(event, data)


# =====================================================================
# 6. ГЕНЕРАТОРЫ КЛАВИАТУР (KEYBOARD ENGINE)
# =====================================================================

class KeyboardManager:
    """Генератор всех Reply и Inline клавиатур для ЛС и групповых чатов."""

    @staticmethod
    def get_main_dm_keyboard() -> ReplyKeyboardMarkup:
        """Главное меню управления в личных сообщениях с ботом."""
        kb = [
            [KeyboardButton(text="👤 Профиль"), KeyboardButton(text="🎒 Инвентарь")],
            [KeyboardButton(text="🗺 Навигация и Зона"), KeyboardButton(text="📜 Квесты и Задания")],
            [KeyboardButton(text="⚖️ Рынок и Аукцион"), KeyboardButton(text="🛡 Мой Клан")],
            [KeyboardButton(text="☢️ Дозиметр"), KeyboardButton(text="ℹ️ Помощь и Инфо")]
        ]
        return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

    @staticmethod
    def get_main_group_keyboard() -> ReplyKeyboardMarkup:
        """Главное меню для групповых чатов (рейдовый режим)."""
        kb = [
            [KeyboardButton(text="🔍 Разведка сектора"), KeyboardButton(text="⚔️ Искать бой")],
            [KeyboardButton(text="📡 Состояние базы"), KeyboardButton(text="🌋 Сканировать аномалии")]
        ]
        return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

    @staticmethod
    def get_profile_inline_kb() -> InlineKeyboardMarkup:
        """Инлайн-панель действий в профиле."""
        buttons = [
            [
                InlineKeyboardButton(text="💪 Характеристики", callback_data="profile_stats"),
                InlineKeyboardButton(text="🏆 Достижения", callback_data="profile_achievements")
            ],
            [
                InlineKeyboardButton(text="🎒 Открыть инвентарь", callback_data="open_inventory_0"),
                InlineKeyboardButton(text="🔄 Обновить", callback_data="refresh_profile")
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def get_inventory_inline_kb(
        items: List[InventoryItem],
        page: int = 0,
        items_per_page: int = 5
    ) -> InlineKeyboardMarkup:
        """Инлайн-клавиатура пагинации предметов инвентаря."""
        buttons = []
        total_items = len(items)
        start_idx = page * items_per_page
        end_idx = start_idx + items_per_page
        current_page_items = items[start_idx:end_idx]

        for item in current_page_items:
            eq_status = " ⚔️[Экипирован]" if item.is_equipped else ""
            item_btn_text = f"{item.template_id} (x{item.quantity}){eq_status}"
            buttons.append([
                InlineKeyboardButton(
                    text=item_btn_text,
                    callback_data=f"item_detail_{item.id}"
                )
            ])

        # Кнопки пагинации
        nav_buttons = []
        if page > 0:
            nav_buttons.append(InlineKeyboardButton(text="⬅️ Назад", callback_data=f"open_inventory_{page - 1}"))

        total_pages = (total_items + items_per_page - 1) // items_per_page if total_items > 0 else 1
        nav_buttons.append(InlineKeyboardButton(text=f"📄 {page + 1}/{total_pages}", callback_data="noop"))

        if end_idx < total_items:
            nav_buttons.append(InlineKeyboardButton(text="Вперед ➡️", callback_data=f"open_inventory_{page + 1}"))

        if nav_buttons:
            buttons.append(nav_buttons)

        buttons.append([InlineKeyboardButton(text="🔙 Вернуться в профиль", callback_data="refresh_profile")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def get_item_action_kb(item: InventoryItem) -> InlineKeyboardMarkup:
        """Меню взаимодействия с отдельным предметом."""
        buttons = []
        if not item.is_equipped:
            buttons.append([InlineKeyboardButton(text="⚔️ Экипировать / Использовать", callback_data=f"item_use_{item.id}")])
        else:
            buttons.append([InlineKeyboardButton(text="🛑 Снять экипировку", callback_data=f"item_unequip_{item.id}")])

        buttons.append([
            InlineKeyboardButton(text="⚖️ Выставить на аукцион", callback_data=f"item_sell_{item.id}"),
            InlineKeyboardButton(text="🗑 Выбросить", callback_data=f"item_drop_{item.id}")
        ])
        buttons.append([InlineKeyboardButton(text="🔙 В инвентарь", callback_data="open_inventory_0")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def get_combat_inline_kb(combat_session_id: int) -> InlineKeyboardMarkup:
        """Панель управления боем (PvE / PvP)."""
        buttons = [
            [
                InlineKeyboardButton(text="💥 Выстрел / Атака", callback_data=f"combat_attack_{combat_session_id}"),
                InlineKeyboardButton(text="💊 Аптечка", callback_data=f"combat_heal_{combat_session_id}")
            ],
            [
                InlineKeyboardButton(text="🛡 Защита / Укрытие", callback_data=f"combat_defend_{combat_session_id}"),
                InlineKeyboardButton(text="🏃 Побег с поля боя", callback_data=f"combat_flee_{combat_session_id}")
            ]
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def get_navigation_inline_kb(connected_locs: List[Dict[str, str]]) -> InlineKeyboardMarkup:
        """Динамическая панель перемещения между локациями."""
        buttons = []
        for loc in connected_locs:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🥾 Перейти в {loc['name']} [{loc['danger_level']}]",
                    callback_data=f"move_to_{loc['id']}"
                )
            ])
        buttons.append([InlineKeyboardButton(text="🔍 Осмотреть текущую локацию", callback_data="inspect_current_loc")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def get_auction_main_kb() -> InlineKeyboardMarkup:
        """Главное меню аукционной системы."""
        buttons = [
            [
                InlineKeyboardButton(text="🛒 Просмотр лотов", callback_data="auction_browse_0"),
                InlineKeyboardButton(text="📦 Мои лоты", callback_data="auction_my_lots")
            ],
            [InlineKeyboardButton(text="➕ Продать предмет", callback_data="open_inventory_0")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def get_clan_menu_kb(has_clan: bool, is_leader: bool = False) -> InlineKeyboardMarkup:
        """Клавиатура кланового меню."""
        if not has_clan:
            buttons = [
                [InlineKeyboardButton(text="🛡 Список кланов Зоны", callback_data="clan_list_0")],
                [InlineKeyboardButton(text="➕ Создать клан (10,000 RU)", callback_data="clan_create")]
            ]
        else:
            buttons = [
                [
                    InlineKeyboardButton(text="👥 Состав клана", callback_data="clan_members"),
                    InlineKeyboardButton(text="💰 Казна клана", callback_data="clan_treasury")
                ]
            ]
            if is_leader:
                buttons.append([InlineKeyboardButton(text="⚙️ Управление кланом", callback_data="clan_admin")])
            buttons.append([InlineKeyboardButton(text="🚪 Покинуть клан", callback_data="clan_leave")])

        return InlineKeyboardMarkup(inline_keyboard=buttons)
# =====================================================================
# 7. ИСТОРИЧЕСКИЙ АРХИВ, ХРОНИКИ ЧАЭС И ЭНЦИКЛОПЕДИЯ ЗОНЫ
# =====================================================================

HISTORICAL_ARCHIVE: Dict[str, Dict[str, Any]] = {
    "chaes_construction": {
        "title": "🏗 Строительство ЧАЭС и г. Припять (1970–1977)",
        "category": "История ЧАЭС",
        "text": (
            "<b>4 февраля 1970 года</b> начались первые работы по выемке грунта под котлован "
            "будущей Чернобыльской АЭС и заложен первый камень города энергетиков — Припяти.\n\n"
            "• <b>27 мая 1970 г.</b> — уложен первый кубометр бетона в фундамент энергоблока №1.\n"
            "• <b>14 декабря 1971 г.</b> — утвержден проект 1-й очереди станции на реакторах РБМК-1000.\n"
            "• <b>26 сентября 1977 г.</b> — 1-й энергоблок дал первый ток в энергосистему СССР.\n"
            "К 1983 году на станции функционировали 4 энергоблока суммарной мощностью 4000 МВт."
        )
    },
    "disaster_chronology": {
        "title": "💥 Хроника катастрофы 26 апреля 1986 года",
        "category": "Катастрофа 1986",
        "text": (
            "<b>Хронология событий 25–26 апреля 1986 года на 4-м энергоблоке ЧАЭС:</b>\n\n"
            "• <b>25 апреля, 01:06</b> — Начало снижения мощности реактора для проведения испытаний.\n"
            "• <b>26 апреля, 00:28</b> — Падение мощности реактора до 30 МВт (тепловых) из-за ксенонового отравления.\n"
            "• <b>01:23:04</b> — Начало испытаний режима выбега ротора турбогенератора №8.\n"
            "• <b>01:23:40</b> — Нажата кнопка аварийной защиты АЗ-5. Ввод стержней управления.\n"
            "• <b>01:23:44</b> — Концевой эффект стержней вызвал всплеск реактивности. Первый взрыв.\n"
            "• <b>01:23:47</b> — Втором взрыв разрушил активную зону и здание 4-го энергоблока.\n"
            "• <b>01:30:00</b> — Прибытие караула ВПЧ-2 во главе с лейтенантом Владимиром Правиком."
        )
    },
    "liquidation_heroism": {
        "title": "🛡 Подвиг ликвидаторов и объект «Укрытие»",
        "category": "Ликвидация",
        "text": (
            "С мая по ноябрь 1986 года в ликвидации последствий аварии приняли участие более <b>600 000 человек</b>.\n\n"
            "• <b>24–25 мая 1986 г.</b> — Разведка подреакторных помещений и сброс воды из бассейна-барботера (трио «дозиметристов-водолазов» Ananenko, Bezpalov, Baranov).\n"
            "• <b>Сентябрь 1986 г.</b> — Очистка крыши 3-го блока («биороботы» в свинцовых фартуках).\n"
            "• <b>30 ноября 1986 г.</b> — Завершен уникальный объект «Укрытие» (Первый Саркофаг)."
        )
    },
    "new_safe_confinement": {
        "title": "🏗 Новый Безопасный Конфайнмент (НБК «Арка»)",
        "category": "Современность",
        "text": (
            "<b>Ноябрь 2016 года</b> — Уникальное инженерное сооружение НБК было надвинуто на устаревший Саркофаг.\n\n"
            "• <b>Пролет арки:</b> 257 метров\n"
            "• <b>Высота:</b> 110 метров (выше статуи Свободы)\n"
            "• <b>Длина:</b> 165 метров\n"
            "• <b>Общий вес:</b> 36 000 тонн\n"
            "Конструкция рассчитана на 100 лет безопасной эксплуатации и позволяет приступить к демонтажу нестабильных конструкций 4-го блока."
        )
    }
}

# =====================================================================
# 8. СПРАВОЧНИК АНОМАЛИЙ ЗОНЫ
# =====================================================================

ANOMALIES_DATABASE: Dict[str, Dict[str, Any]] = {
    "trampoline": {
        "name": "🌀 Трамплин",
        "type": "Гравитационная",
        "danger_level": "Средний",
        "description": "Одна из первых зарегистрированных аномалий. Наносит урон гравитационным ударом.",
        "damage": 45.0,
        "detector_required": "detector_echo",
        "possible_artifacts": ["art_medusa", "art_stone_flower"]
    },
    "carousel": {
        "name": "🌪 Карусель",
        "type": "Гравитационная",
        "danger_level": "Высокий",
        "description": "Поднимает жертву в воздух, раскручивает до огромной скорости и разрывает.",
        "damage": 85.0,
        "detector_required": "detector_bear",
        "possible_artifacts": ["art_night_star", "art_vyvert"]
    },
    "burner": {
        "name": "🔥 Жарка",
        "type": "Термическая",
        "danger_level": "Высокий",
        "description": "В спокойном состоянии незаметна. При разряде выбрасывает столб пламени температурой до 1500°C.",
        "damage": 70.0,
        "detector_required": "detector_bear",
        "possible_artifacts": ["art_droplet", "art_fireball", "art_crystal"]
    },
    "electro": {
        "name": "⚡ Электра",
        "type": "Электрическая",
        "danger_level": "Высокий",
        "description": "Сгусток статического электричества. При детонации разряжается мощнейшей дугой.",
        "damage": 65.0,
        "detector_required": "detector_veles",
        "possible_artifacts": ["art_sparkler", "art_flash", "art_battery"]
    },
    "fruit_punch": {
        "name": "☣️ Газировка (Кислотный кисель)",
        "type": "Химическая",
        "danger_level": "Средний",
        "description": "Аномальная лужа светящейся кислоты. Разъедает броню и органику за секунды.",
        "damage": 50.0,
        "detector_required": "detector_echo",
        "possible_artifacts": ["art_slime", "art_slug", "art_mica"]
    }
}

# =====================================================================
# 9. ЭТАЛОННАЯ БАЗА ШАБЛОНОВ ПРЕДМЕТОВ (ITEM TEMPLATES SEED DATA)
# =====================================================================

ITEM_TEMPLATES_DATABASE: List[Dict[str, Any]] = [
    # ------------------ ОРУЖИЕ ------------------
    {
        "id": "weapon_pm",
        "name": "Пистолет Макарова (ПМ)",
        "description": "Надежный и дешевый пистолет под патрон 9x18 мм. Оружие новичка.",
        "item_type": ItemType.WEAPON,
        "rarity": ItemRarity.COMMON,
        "base_price": 1200,
        "weight": 0.73,
        "stats": {"damage": 18.0, "accuracy": 0.65, "fire_rate": 1.2, "ammo_type": "ammo_9x18"}
    },
    {
        "id": "weapon_ak74u",
        "name": "АКС-74У",
        "description": "Укороченный автомат под патрон 5.45x39 мм. Отличный выбор для ближнего боя.",
        "item_type": ItemType.WEAPON,
        "rarity": ItemRarity.UNCOMMON,
        "base_price": 4500,
        "weight": 2.7,
        "stats": {"damage": 35.0, "accuracy": 0.72, "fire_rate": 2.5, "ammo_type": "ammo_545"}
    },
    {
        "id": "weapon_ak74",
        "name": "АК-74",
        "description": "Классический советский автомат. Рабочая лошадка большинства сталкеров Зоны.",
        "item_type": ItemType.WEAPON,
        "rarity": ItemRarity.UNCOMMON,
        "base_price": 7800,
        "weight": 3.3,
        "stats": {"damage": 42.0, "accuracy": 0.80, "fire_rate": 2.8, "ammo_type": "ammo_545"}
    },
    {
        "id": "weapon_gauss",
        "name": "Гаусс-пушка (Изделие 62)",
        "description": "Сверхточная винтовка, использующая энергию вспышек артефактов для разгона пули.",
        "item_type": ItemType.WEAPON,
        "rarity": ItemRarity.LEGENDARY,
        "base_price": 85000,
        "weight": 5.5,
        "stats": {"damage": 220.0, "accuracy": 0.99, "fire_rate": 0.5, "ammo_type": "ammo_gauss"}
    },

    # ------------------ БРОНЯ ------------------
    {
        "id": "armor_jacket",
        "name": "Кожаная куртка",
        "description": "Обычная плотная куртка с подкладкой. Защищает разве что от собачьих укусов и легкого сквозняка.",
        "item_type": ItemType.ARMOR,
        "rarity": ItemRarity.COMMON,
        "base_price": 800,
        "weight": 3.0,
        "stats": {"armor_val": 10.0, "rad_prot": 5.0, "anom_prot": 5.0}
    },
    {
        "id": "armor_stalker",
        "name": "Комбинезон «Заря»",
        "description": "Легендарный комбинезон сталкера. Хороший баланс пулестойкости и аномальной защиты.",
        "item_type": ItemType.ARMOR,
        "rarity": ItemRarity.RARE,
        "base_price": 15000,
        "weight": 7.0,
        "stats": {"armor_val": 45.0, "rad_prot": 35.0, "anom_prot": 30.0}
    },
    {
        "id": "armor_seva",
        "name": "Костюм «СЕВА»",
        "description": "Комбинезон с замкнутой системой дыхания для работы в условиях высокой радиации и аномалий.",
        "item_type": ItemType.ARMOR,
        "rarity": ItemRarity.EPIC,
        "base_price": 42000,
        "weight": 9.0,
        "stats": {"armor_val": 35.0, "rad_prot": 85.0, "anom_prot": 80.0}
    },
    {
        "id": "armor_exoskeleton",
        "name": "Экзоскелет",
        "description": "Тяжелый бронекостюм с гидравлическими сервоприводами. Позволяет переносить огромные грузы.",
        "item_type": ItemType.ARMOR,
        "rarity": ItemRarity.LEGENDARY,
        "base_price": 110000,
        "weight": 25.0,
        "stats": {"armor_val": 95.0, "rad_prot": 50.0, "anom_prot": 40.0}
    },

    # ------------------ ДЕТЕКТОРЫ ------------------
    {
        "id": "detector_echo",
        "name": "Детектор «Отклик»",
        "description": "Базовый дозиметрический прибор. Подает звуковой сигнал при приближении к аномалиям.",
        "item_type": ItemType.DETECTOR,
        "rarity": ItemRarity.COMMON,
        "base_price": 1500,
        "weight": 0.5,
        "stats": {"scan_range": 10.0, "detection_tier": 1}
    },
    {
        "id": "detector_veles",
        "name": "Детектор «Велес»",
        "description": "Современный сканер. Отображает точное положение артефактов и аномальных полей на дисплее.",
        "item_type": ItemType.DETECTOR,
        "rarity": ItemRarity.EPIC,
        "base_price": 18000,
        "weight": 0.8,
        "stats": {"scan_range": 35.0, "detection_tier": 3}
    },

    # ------------------ МЕДИКАМЕНТЫ И ЕДА ------------------
    {
        "id": "medkit_basic",
        "name": "Аптечка индивидуальная",
        "description": "Стандартный набор для оказания первой помощи. Восстанавливает здоровье.",
        "item_type": ItemType.MEDICINE,
        "rarity": ItemRarity.COMMON,
        "base_price": 300,
        "weight": 0.3,
        "stats": {"heal_hp": 40.0, "cure_bleed": 0.2}
    },
    {
        "id": "antirad",
        "name": "Препарат «Антирад»",
        "description": "Эффективное средство для выведения радиоактивных изотопов из организма.",
        "item_type": ItemType.MEDICINE,
        "rarity": ItemRarity.UNCOMMON,
        "base_price": 500,
        "weight": 0.1,
        "stats": {"reduce_rad": 50.0}
    },
    {
        "id": "food_tourist",
        "name": "Консервы «Завтрак туриста»",
        "description": "Сытная тушенка. Утоляет голод и немного поднимает выносливость.",
        "item_type": ItemType.FOOD,
        "rarity": ItemRarity.COMMON,
        "base_price": 150,
        "weight": 0.5,
        "stats": {"reduce_hunger": 40.0, "stamina_boost": 10.0}
    },

    # ------------------ АРТЕФАКТЫ ------------------
    {
        "id": "art_medusa",
        "name": "Артефакт «Медуза»",
        "description": "Формируется в аномалии Трамплин. Поглощает пулевые ранения, но слегка фонит.",
        "item_type": ItemType.ARTIFACT,
        "rarity": ItemRarity.UNCOMMON,
        "base_price": 3500,
        "weight": 0.5,
        "stats": {"armor_bonus": 10.0, "rad_emission": 2.0}
    },
    {
        "id": "art_night_star",
        "name": "Артефакт «Ночная звезда»",
        "description": "Редкий гравитационный артефакт. Создает вокруг владельца локальное силовое поле.",
        "item_type": ItemType.ARTIFACT,
        "rarity": ItemRarity.EPIC,
        "base_price": 12500,
        "weight": 0.5,
        "stats": {"armor_bonus": 25.0, "rad_emission": 5.0}
    }
]

# =====================================================================
# 10. СЕРВИС УПРАВЛЕНИЯ СПРАВОЧНИКОМ И ИНИЦИАЛИЗАЦИЯ
# =====================================================================

class EncyclopediaService:
    """Сервис доступа к архивным данным и заполнения базы шаблонами предметов."""

    @staticmethod
    async def seed_item_templates(session: AsyncSession) -> None:
        """Автоматическое наполнение базы данных шаблонами предметов при запуске."""
        for item_data in ITEM_TEMPLATES_DATABASE:
            stmt = select(ItemTemplate).where(ItemTemplate.id == item_data["id"])
            res = await session.execute(stmt)
            existing = res.scalar_one_or_none()

            if not existing:
                template = ItemTemplate(
                    id=item_data["id"],
                    name=item_data["name"],
                    description=item_data["description"],
                    item_type=item_data["item_type"],
                    rarity=item_data["rarity"],
                    base_price=item_data["base_price"],
                    weight=item_data["weight"],
                    stats_json=item_data["stats"]
                )
                session.add(template)
        await session.commit()
        logger.info("База данных шаблонов предметов успешно обновлена и заполнена.")

    @staticmethod
    def get_archive_article(article_key: str) -> Optional[Dict[str, Any]]:
        return HISTORICAL_ARCHIVE.get(article_key)

    @staticmethod
    def get_anomaly_info(anomaly_key: str) -> Optional[Dict[str, Any]]:
        return ANOMALIES_DATABASE.get(anomaly_key)
# =====================================================================
# 11. РАСШИРЕННЫЙ БАНК ВОПРОСОВ ВИКТОРИНЫ И ИНТЕЛЛЕКТУАЛЬНЫХ КВЕСТОВ
# =====================================================================

QUIZ_DATABASE: List[Dict[str, Any]] = [
    # --- РЕАЛЬНАЯ ИСТОРИЯ ЧАЭС И КАТАСТРОФЫ 1986 ГОДА ---
    {
        "id": "q_chaes_01",
        "category": "История ЧАЭС",
        "question": "В каком году Чернобыльская АЭС дала первый промышленный ток в энергосистему СССР?",
        "options": ["1970 г.", "1975 г.", "1977 г.", "1983 г."],
        "correct": 2,
        "explanation": (
            "26 сентября 1977 года первый энергоблок ЧАЭС с реактором РБМК-1000 "
            "был успешно подсоединен к энергосистеме СССР."
        ),
        "reward_rubles": 150,
        "reward_exp": 50
    },
    {
        "id": "q_chaes_02",
        "category": "Физика и Катастрофа",
        "question": "Какая кнопка аварийной защиты была нажата на 4-м энергоблоке перед взрывом в 01:23:40?",
        "options": ["АЗ-5", "БЩУ-4", "АВАРИЯ-1", "ПЗ-2"],
        "correct": 0,
        "explanation": (
            "Кнопка АЗ-5 (Аварийная Защита 5-й категории) вызывает полный сброс "
            "всех регулирующих стержней в активную зону реактора."
        ),
        "reward_rubles": 200,
        "reward_exp": 75
    },
    {
        "id": "q_chaes_03",
        "category": "История ЧАЭС",
        "question": "Как расшифровывается аббревиатура типа реактора РБМК, установленного на ЧАЭС?",
        "options": [
            "Реактор Быстрого Магнитного Контура",
            "Реактор Большой Мощности Канальный",
            "Радиационный Безопасный Модульный Котел",
            "Реактор Базовый Металлический Канальный"
        ],
        "correct": 1,
        "explanation": (
            "РБМК — Реактор Большой Мощности Канальный. Это гетерогенный "
            "канальный графито-водный ядерный реактор."
        ),
        "reward_rubles": 180,
        "reward_exp": 60
    },
    {
        "id": "q_chaes_04",
        "category": "Ликвидация",
        "question": "Как называли ликвидаторов, руками очищавших крышу 3-го энергоблока от радиоактивного графита?",
        "options": ["«Стальные волки»", "«Биороботы»", "«Ликвидаторы-100»", "«Графитчики»"],
        "correct": 1,
        "explanation": (
            "Из-за того что робототехника выходила из строя от высочайшей радиации, "
            "задачи выполняли люди, которых неофициально прозвали «биороботами»."
        ),
        "reward_rubles": 250,
        "reward_exp": 100
    },
    {
        "id": "q_chaes_05",
        "category": "Современность ЧАЭС",
        "question": "Какова высота Нового Безопасного Конфайнмента (НБК «Арка»), надвинутого на Саркофаг в 2016 году?",
        "options": ["75 метров", "92 метра", "110 метров", "145 метров"],
        "correct": 2,
        "explanation": (
            "Высота НБК «Арка» составляет 110 метров, что выше американской Статуи Свободы "
            "и биг-бена в Лондоне."
        ),
        "reward_rubles": 220,
        "reward_exp": 80
    },
    {
        "id": "q_chaes_06",
        "category": "География Зоны",
        "question": "В каком году был основан город Припять?",
        "options": ["1965 г.", "1970 г.", "1977 г.", "1986 г."],
        "correct": 1,
        "explanation": (
            "Город Припять был основан 4 февраля 1970 года как IX союзный ударный "
            "комсомольский город-спутник АЭС."
        ),
        "reward_rubles": 150,
        "reward_exp": 50
    },

    # --- ЛОР И МЕХАНИКИ ВСЕЛЕННОЙ STALKER ---
    {
        "id": "q_lore_01",
        "category": "Аномалии и Артефакты",
        "question": "В какой гравитационной аномалии чаще всего порождается артефакт «Медуза»?",
        "options": ["Карусель", "Трамплин", "Воронка", "Жарка"],
        "correct": 1,
        "explanation": (
            "Артефакт «Медуза» формируется аномалией «Трамплин». "
            "Он сжимает вокруг себя пулевые повреждения, но создает слабое излучение."
        ),
        "reward_rubles": 120,
        "reward_exp": 40
    },
    {
        "id": "q_lore_02",
        "category": "Вооружение Зоны",
        "question": "Какой секретный проект обозначался в документах как «Изделие 62»?",
        "options": ["Детектор Велес", "Гаусс-пушка", "Экзоскелет «Монолита»", "Установка Выжигатель Ума"],
        "correct": 1,
        "explanation": (
            "«Изделие 62» — это официальное секретное название Гаусс-пушки, "
            "высокоточного электромагнитного оружия Зоны."
        ),
        "reward_rubles": 300,
        "reward_exp": 120
    },
    {
        "id": "q_lore_03",
        "category": "Группировки Зоны",
        "question": "Какая группировка ставит своей главной целью уничтожение Зоны и всех ее проявлений?",
        "options": ["«Свобода»", "«Долг»", "«Чистое Небо»", "«Ученые»"],
        "correct": 1,
        "explanation": (
            "Группировка «Долг» — военизированный клан, считающий Зону язвой на теле планеты, "
            "которую необходимо уничтожить."
        ),
        "reward_rubles": 140,
        "reward_exp": 45
    },
    {
        "id": "q_lore_04",
        "category": "Легенды Зоны",
        "question": "Как зовут легендарного сталкера, который первым пробрался к центру Зоны в 2011 году?",
        "options": ["Меченый (Стрелок)", "Шрам", "Дегтярев", "Клык"],
        "correct": 0,
        "explanation": (
            "Стрелок (он же Меченый) прошел через Выжигатель Ума и первым достиг "
            "исполнителя желаний и секретных лабораторий ЧАЭС."
        ),
        "reward_rubles": 200,
        "reward_exp": 70
    }
]


# =====================================================================
# 12. СЕРВИС ИНТЕЛЛЕКТУАЛЬНЫХ КВЕСТОВ И ВИКТОРИН (QUIZ ENGINE)
# =====================================================================

class QuizService:
    """Сервис проведения викторин, проверки ответов и выдачи наград."""

    @staticmethod
    def get_random_question(exclude_ids: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
        """Возвращает случайный вопрос из базы, исключая ранее пройденные."""
        available = QUIZ_DATABASE
        if exclude_ids:
            available = [q for q in QUIZ_DATABASE if q["id"] not in exclude_ids]

        if not available:
            # Если все вопросы пройдены, сбрасываем и берем из полного списка
            available = QUIZ_DATABASE

        return random.choice(available) if available else None

    @staticmethod
    def get_question_by_id(question_id: str) -> Optional[Dict[str, Any]]:
        """Поиск вопроса по ID."""
        for q in QUIZ_DATABASE:
            if q["id"] == question_id:
                return q
        return None

    @staticmethod
    async def process_answer(
        session: AsyncSession,
        player: Player,
        question_id: str,
        selected_option: int
    ) -> Tuple[bool, str, Dict[str, int]]:
        """
        Проверяет ответ игрока, начисляет рубли и опыт, формирует пояснение.
        """
        q_data = QuizService.get_question_by_id(question_id)
        if not q_data:
            return False, "⚠️ Вопрос не найден в архивах КПК.", {"rubles": 0, "exp": 0}

        is_correct = (selected_option == q_data["correct"])

        if is_correct:
            rubles_reward = q_data["reward_rubles"]
            exp_reward = q_data["reward_exp"]

            # Выплата награды игроку
            player.rubles += rubles_reward
            player.experience += exp_reward
            await session.commit()

            result_msg = (
                f"✅ <b>ВЕРНО!</b>\n\n"
                f"🧠 <b>Архивная справка:</b>\n{q_data['explanation']}\n\n"
                f"🎁 <b>Награда:</b> +{rubles_reward} RU, +{exp_reward} EXP"
            )
            return True, result_msg, {"rubles": rubles_reward, "exp": exp_reward}
        else:
            correct_text = q_data["options"][q_data["correct"]]
            result_msg = (
                f"❌ <b>НЕВЕРНО!</b>\n\n"
                f"Правильный ответ: <b>{correct_text}</b>\n\n"
                f"🧠 <b>Архивная справка:</b>\n{q_data['explanation']}"
            )
            return False, result_msg, {"rubles": 0, "exp": 0}


# =====================================================================
# 13. КЛАВИАТУРЫ И ХЭНДЛЕРЫ ВИКТОРИНЫ (QUIZ ROUTER)
# =====================================================================

class QuizKeyboardManager:
    """Генератор инлайн-клавиатур для викторин."""

    @staticmethod
    def get_question_keyboard(question: Dict[str, Any]) -> InlineKeyboardMarkup:
        buttons = []
        q_id = question["id"]

        for idx, option in enumerate(question["options"]):
            buttons.append([
                InlineKeyboardButton(
                    text=f"{chr(65 + idx)}) {option}",
                    callback_data=f"quiz_ans_{q_id}_{idx}"
                )
            ])

        buttons.append([InlineKeyboardButton(text="🚪 Отмена", callback_data="quiz_cancel")])
        return InlineKeyboardMarkup(inline_keyboard=buttons)

    @staticmethod
    def get_next_question_keyboard() -> InlineKeyboardMarkup:
        buttons = [
            [InlineKeyboardButton(text="❓ Следующий вопрос", callback_data="quiz_start")],
            [InlineKeyboardButton(text="🔙 В главное меню", callback_data="refresh_profile")]
        ]
        return InlineKeyboardMarkup(inline_keyboard=buttons)


quiz_router = Router(name="quiz_router")


@quiz_router.message(F.text == "📜 Квесты и Задания")
@quiz_router.message(Command("quiz"))
async def cmd_start_quiz(message: Message, db_user: Player, session: AsyncSession):
    """Запуск интеллектуального квеста из меню или по команде."""
    question = QuizService.get_random_question()
    if not question:
        await message.answer("⚠️ База данных викторины пуста или недоступна.")
        return

    text = (
        f"📜 <b>ИНТЕЛЛЕКТУАЛЬНЫЙ КВЕСТ КПК</b>\n"
        f"Категория: <i>{question['category']}</i>\n"
        f"Уровень сложности: 🟨 Средний\n\n"
        f"<b>Вопрос:</b>\n{question['question']}\n\n"
        f"💰 Награда: <b>{question['reward_rubles']} RU</b> | ⚡️ <b>{question['reward_exp']} EXP</b>"
    )

    await message.answer(
        text=text,
        reply_markup=QuizKeyboardManager.get_question_keyboard(question),
        parse_mode="HTML"
    )


@quiz_router.callback_query(F.data == "quiz_start")
async def cb_quiz_start(callback: CallbackQuery, db_user: Player):
    """Callback-хэндлер перехода к следующему вопросу."""
    question = QuizService.get_random_question()
    if not question:
        await callback.answer("⚠️ База вопросов недоступна.", show_alert=True)
        return

    text = (
        f"📜 <b>ИНТЕЛЛЕКТУАЛЬНЫЙ КВЕСТ КПК</b>\n"
        f"Категория: <i>{question['category']}</i>\n\n"
        f"<b>Вопрос:</b>\n{question['question']}\n\n"
        f"💰 Награда: <b>{question['reward_rubles']} RU</b> | ⚡️ <b>{question['reward_exp']} EXP</b>"
    )

    await callback.message.edit_text(
        text=text,
        reply_markup=QuizKeyboardManager.get_question_keyboard(question),
        parse_mode="HTML"
    )
    await callback.answer()


@quiz_router.callback_query(F.data.startswith("quiz_ans_"))
async def cb_quiz_answer(callback: CallbackQuery, db_user: Player, session: AsyncSession):
    """Обработка выбранного варианта ответа."""
    parts = callback.data.split("_")
    q_id = f"{parts[2]}_{parts[3]}"
    selected_idx = int(parts[4])

    is_correct, response_text, rewards = await QuizService.process_answer(
        session=session,
        player=db_user,
        question_id=q_id,
        selected_option=selected_idx
    )

    await callback.message.edit_text(
        text=response_text,
        reply_markup=QuizKeyboardManager.get_next_question_keyboard(),
        parse_mode="HTML"
    )
    await callback.answer("Ответ принят!" if is_correct else "Ошибка!", show_alert=False)


@quiz_router.callback_query(F.data == "quiz_cancel")
async def cb_quiz_cancel(callback: CallbackQuery):
    """Отмена викторины."""
    await callback.message.edit_text("🛑 Сеанс викторины завершен.")
    await callback.answer()
# =====================================================================
# 14. МАРШРУТИЗАТОРЫ (ROUTERS) И ОСНОВНЫЕ ХЭНДЛЕРЫ
# =====================================================================

main_router = Router(name="main_router")
player_router = Router(name="player_router")
admin_router = Router(name="admin_router")


# =====================================================================
# 15. КОМАНДЫ И ОБРАБОТЧИКИ ДЛЯ ИГРОКОВ (PLAYER HANDLERS)
# =====================================================================

@player_router.message(CommandStart())
async def cmd_start(message: Message, db_user: Player):
    """Стартовая команда /start."""
    welcome_text = (
        f"☢️ <b>Добро пожаловать в Зону Отчуждения, {db_user.full_name}!</b>\n\n"
        f"Ты стоишь на пороге Кордона. В твоем КПК активированы базовые модули навигации, "
        f"торговли и боевого взаимодействия.\n\n"
        f"🆔 Ваш ID: <code>{db_user.telegram_id}</code>\n"
        f"📊 Уровень: <b>{db_user.level}</b> | Опыт: <b>{db_user.experience}</b>\n"
        f"💰 Баланс: <b>{db_user.rubles} RU</b>\n\n"
        f"Используй меню ниже для управления своим персонажем."
    )
    
    if message.chat.type in [ChatType.GROUP, ChatType.SUPERGROUP]:
        await message.answer(
            welcome_text,
            reply_markup=KeyboardManager.get_main_group_keyboard(),
            parse_mode="HTML"
        )
    else:
        await message.answer(
            welcome_text,
            reply_markup=KeyboardManager.get_main_dm_keyboard(),
            parse_mode="HTML"
        )


@player_router.message(F.text == "👤 Профиль")
@player_router.message(Command("profile"))
async def cmd_profile(message: Message, db_user: Player, session: AsyncSession):
    """Отображение профиля игрока."""
    inventory_count = await PlayerService.get_inventory_count(session, db_user.id)
    
    profile_text = (
        f"👤 <b>ПРОФИЛЬ СТАЛКЕРА</b>\n"
        f"═════════════════════\n"
        f"🪪 Позывной: <b>{db_user.full_name}</b>\n"
        f"🔰 Группировка: <b>{db_user.faction}</b>\n"
        f"⭐ Уровень: <b>{db_user.level}</b> (EXP: {db_user.experience})\n"
        f"❤️ Здоровье: <b>{db_user.health:.1f}/100.0</b>\n"
        f"☢️ Радиация: <b>{db_user.radiation:.1f}%</b>\n"
        f"💰 Валюта: <b>{db_user.rubles:,} RU</b>\n"
        f"📍 Локация: <b>{db_user.current_location_id.capitalize()}</b>\n"
        f"🎒 Рюкзак: <b>{inventory_count}/30 предметов</b>\n"
        f"═════════════════════\n"
        f"⚔️ Урон: <b>{db_user.stats_damage}</b> | 🛡 Броня: <b>{db_user.stats_defense}</b>"
    )
    
    await message.answer(
        text=profile_text,
        reply_markup=KeyboardManager.get_profile_inline_kb(),
        parse_mode="HTML"
    )


@player_router.callback_query(F.data == "refresh_profile")
async def cb_refresh_profile(callback: CallbackQuery, db_user: Player, session: AsyncSession):
    """Обновление данных профиля."""
    inventory_count = await PlayerService.get_inventory_count(session, db_user.id)
    
    profile_text = (
        f"👤 <b>ПРОФИЛЬ СТАЛКЕРА</b>\n"
        f"═════════════════════\n"
        f"🪪 Позывной: <b>{db_user.full_name}</b>\n"
        f"🔰 Группировка: <b>{db_user.faction}</b>\n"
        f"⭐ Уровень: <b>{db_user.level}</b> (EXP: {db_user.experience})\n"
        f"❤️ Здоровье: <b>{db_user.health:.1f}/100.0</b>\n"
        f"☢️ Радиация: <b>{db_user.radiation:.1f}%</b>\n"
        f"💰 Валюта: <b>{db_user.rubles:,} RU</b>\n"
        f"📍 Локация: <b>{db_user.current_location_id.capitalize()}</b>\n"
        f"🎒 Рюкзак: <b>{inventory_count}/30 предметов</b>\n"
        f"═════════════════════\n"
        f"⚔️ Урон: <b>{db_user.stats_damage}</b> | 🛡 Броня: <b>{db_user.stats_defense}</b>"
    )
    
    try:
        await callback.message.edit_text(
            text=profile_text,
            reply_markup=KeyboardManager.get_profile_inline_kb(),
            parse_mode="HTML"
        )
    except Exception:
        pass
    await callback.answer("Данные обновлены")


@player_router.message(F.text == "🎒 Инвентарь")
@player_router.message(Command("inventory"))
async def cmd_inventory(message: Message, db_user: Player, session: AsyncSession):
    """Просмотр инвентаря игрока."""
    items = await PlayerService.get_player_inventory(session, db_user.id)
    
    if not items:
        await message.answer(
            "🎒 <b>Ваш рюкзак пуст.</b>\nИсследуйте Зону или выполняйте квесты, чтобы найти снаряжение.",
            parse_mode="HTML"
        )
        return

    inv_text = f"🎒 <b>ИНВЕНТАРЬ (Предметов: {len(items)}):</b>\n Выберите предмет для взаимодействия:"
    await message.answer(
        text=inv_text,
        reply_markup=KeyboardManager.get_inventory_inline_kb(items, page=0),
        parse_mode="HTML"
    )


@player_router.callback_query(F.data.startswith("open_inventory_"))
async def cb_open_inventory(callback: CallbackQuery, db_user: Player, session: AsyncSession):
    """Пагинация инвентаря."""
    page = int(callback.data.split("_")[2])
    items = await PlayerService.get_player_inventory(session, db_user.id)
    
    inv_text = f"🎒 <b>ИНВЕНТАРЬ (Предметов: {len(items)}):</b>\n Страница {page + 1}"
    await callback.message.edit_text(
        text=inv_text,
        reply_markup=KeyboardManager.get_inventory_inline_kb(items, page=page),
        parse_mode="HTML"
    )
    await callback.answer()


@player_router.message(F.text == "☢️ Дозиметр")
async def cmd_dosimeter(message: Message, db_user: Player):
    """Проверка радиоактивного фона и текущего заражения."""
    text = (
        f"☢️ <b>ПОКАЗАНИЯ ДОЗИМЕТРА ДП-5В</b>\n"
        f"═════════════════════════\n"
        f"Текущий уровень заражения: <b>{db_user.radiation:.2f} мР/ч</b>\n\n"
    )
    if db_user.radiation < 10:
        text += "🟢 <i>Радиационный фон в норме. Опасности нет.</i>"
    elif db_user.radiation < 50:
        text += "🟡 <i>Повышенный фон! Рекомендуется принять Антирад.</i>"
    else:
        text += "🔴 <i>ОПАСНЫЙ УРОВЕНЬ РАДИАЦИИ! Срочно требуется дезактивация!</i>"

    await message.answer(text, parse_mode="HTML")


@player_router.message(F.text == "ℹ️ Помощь и Инфо")
async def cmd_help(message: Message):
    """Справочная информация и инструкции."""
    help_text = (
        "📖 <b>СПРАВОЧНИК СТАЛКЕРА</b>\n\n"
        "• <b>👤 Профиль</b> — статистика персонажа, здоровье, группировка.\n"
        "• <b>🎒 Инвентарь</b> — экипировка оружия, брони, применение медикаментов.\n"
        "• <b>📜 Квесты и Задания</b> — интеллектуальные викторины по истории ЧАЭС и Зоны.\n"
        "• <b>⚖️ Рынок и Аукцион</b> — торговля редким лутом с другими игроками.\n"
        "• <b>🛡 Мой Клан</b> — объединение в кланы и захват секторов.\n\n"
        "💬 <i>Команды в групповых чатах:</i>\n"
        "/quiz — запустить случайный исторический квест\n"
        "/profile — просмотреть карточку сталкера"
    )
    await message.answer(help_text, parse_mode="HTML")


# =====================================================================
# 16. АДМИНИСТРАТИВНЫЕ КОМАНДЫ (ADMIN HANDLERS)
# =====================================================================

ADMIN_IDS = [123456789]  # Укажите Telegram ID администраторов


def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


@admin_router.message(Command("admin"))
async def cmd_admin_panel(message: Message, db_user: Player):
    """Панель управления администратора."""
    if not is_admin(message.from_user.id):
        await message.answer("⛔️ У вас нет доступа к командам Администратора КПК.")
        return

    admin_text = (
        "⚙️ <b>ПАНЕЛЬ АДМИНИСТРАТОРА ЧАЭС MMO</b>\n\n"
        "Доступные команды:\n"
        "• <code>/give_rubles [user_id] [amount]</code> — выдать рубли игроку\n"
        "• <code>/add_item [user_id] [item_template_id]</code> — выдать предмет\n"
        "• <code>/stats</code> — статистика сервера и базы данных\n"
        "• <code>/broadcast [текст]</code> — рассылка всем зарегистрированным сталкерам"
    )
    await message.answer(admin_text, parse_mode="HTML")


@admin_router.message(Command("give_rubles"))
async def cmd_give_rubles(message: Message, session: AsyncSession):
    """Выдача игровой валюты игроку по ID."""
    if not is_admin(message.from_user.id):
        return

    args = message.text.split()
    if len(args) < 3:
        await message.answer("⚠️ Использование: <code>/give_rubles [user_id] [amount]</code>", parse_mode="HTML")
        return

    try:
        target_tg_id = int(args[1])
        amount = int(args[2])
    except ValueError:
        await message.answer("❌ Ошибка: user_id и amount должны быть числами.")
        return

    stmt = select(Player).where(Player.telegram_id == target_tg_id)
    res = await session.execute(stmt)
    target_player = res.scalar_one_or_none()

    if not target_player:
        await message.answer("❌ Игрок с таким ID не найден в базе данных.")
        return

    target_player.rubles += amount
    await session.commit()

    await message.answer(f"✅ Успешно перечислено <b>{amount:,} RU</b> игроку <b>{target_player.full_name}</b>.", parse_mode="HTML")


@admin_router.message(Command("stats"))
async def cmd_server_stats(message: Message, session: AsyncSession):
    """Просмотр глобальной статистики сервера."""
    if not is_admin(message.from_user.id):
        return

    players_cnt = await session.scalar(select(func.count(Player.id)))
    groups_cnt = await session.scalar(select(func.count(GroupChat.id)))
    items_cnt = await session.scalar(select(func.count(InventoryItem.id)))
    clans_cnt = await session.scalar(select(func.count(Clan.id)))

    stats_text = (
        "📊 <b>ГЛОБАЛЬНАЯ СТАТИСТИКА СЕРВЕРА</b>\n\n"
        f"👤 Зарегистрировано сталкеров: <b>{players_cnt}</b>\n"
        f"💬 Активных групповых чатов: <b>{groups_cnt}</b>\n"
        f"🎒 Предметов в инвентарях: <b>{items_cnt}</b>\n"
        f"🛡 Создано кланов: <b>{clans_cnt}</b>"
    )
    await message.answer(stats_text, parse_mode="HTML")


# =====================================================================
# 17. ТОЧКА ВХОДА И ЗАПУСК БОТА (BOT ENTRY POINT & ENGINE LAUNCH)
# =====================================================================

async def main():
    """Главная функция инициализации и запуска бота."""
    BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"  # Токен Telegram Бота

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(name)s - %(message)s",
    )
    logger.info("Инициализация инфраструктуры STALKER ЧАЭС MMO Bot...")

    # Инициализация SQLAlchemy ORM базы данных
    await init_db()

    # Заполнение базы данных шаблонами предметов и заготовок
    async with AsyncSessionLocal() as session:
        await EncyclopediaService.seed_item_templates(session)

    bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()

    # Подключение Middleware компонентов
    dp.update.outer_middleware(DatabaseMiddleware())
    dp.update.middleware(UserRegistrationMiddleware())
    dp.message.middleware(ThrottlingMiddleware(rate_limit=0.8))

    # Регистрация маршрутизаторов (Routers)
    dp.include_router(main_router)
    dp.include_router(player_router)
    dp.include_router(quiz_router)
    dp.include_router(admin_router)

    logger.info("Бот успешно запущен и готов к приему команд в Зоне!")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")

