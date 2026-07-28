import json
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeAlias, TypedDict, Union

# Add project root to sys.path (find the directory containing db_structs.py)
_root = Path(__file__).resolve().parent
while _root.parent != _root:
    if (_root / "db_structs.py").exists():
        if str(_root) not in sys.path:
            sys.path.append(str(_root))
        break
    _root = _root.parent

from db_structs import Circle, Event, EventGroup, is_eg_db

# =====================================================================
#  Type Aliases and TypedDict Schemas
# =====================================================================

# FolderTree represents the recursive directory structure before serialization.
# The keys are folder/db names, and the values are either another FolderTree or a loaded EventGroup.
FolderTree: TypeAlias = dict[str, Union['FolderTree', EventGroup]]

class SerializedEventGroup(TypedDict, total=False):
    """Shape of default-serialized EventGroup JSON dictionary."""
    aliases: list[str]
    events: dict[str, Any] | list[dict[str, Any]]
    sources: list[dict[str, Any]]
    media: list[dict[str, Any]]
    links: list[str]
    comments: str
    description: str
    last_edited: str
    db_name: str
    db_path: str

class SerializedEvent(TypedDict, total=False):
    """Shape of default-serialized Event JSON dictionary."""
    aliases: list[str]
    dates: str
    circles: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    media: list[dict[str, Any]]
    locations: list[dict[str, Any]]
    last_edited: str
    db_name: str
    db_path: str
    event_group_aliases: list[str]

class SerializedCircle(TypedDict, total=False):
    """Shape of default-serialized Circle JSON dictionary."""
    aliases: list[str]
    pen_names: list[str]
    position: str
    sources: list[dict[str, Any]]
    media: list[dict[str, Any]]
    links: list[str]
    comments: str
    description: str
    db_name: str
    db_path: str
    event_aliases: list[str]
    event_group_aliases: list[str]

# =====================================================================
#  Core Wrapper Data Structures
# =====================================================================

@dataclass(slots=True)
class CompleteEventGroup:
    """Wrapper for EventGroup that includes its database metadata."""
    event_group: EventGroup
    db_name: str
    db_path: Path # Relative path from the database root folder

@dataclass(slots=True)
class CompleteEvent:
    """Wrapper for Event that includes its parent EventGroup and database metadata."""
    event: Event
    event_group: EventGroup
    db_name: str
    db_path: Path # Relative path from the database root folder

@dataclass(slots=True)
class CompleteCircle:
    """Wrapper for Circle that includes its parent Event, EventGroup, and database metadata."""
    circle: Circle
    event: Event
    event_group: EventGroup
    db_name: str
    db_path: Path # Relative path from the database root folder


@dataclass(slots=True)
class PrecomputedStructures:
    """Groups the four main precomputed data structures."""
    complete_recursive: FolderTree
    event_group_list: list[CompleteEventGroup]
    event_list: list[CompleteEvent]
    circle_list: list[CompleteCircle]


# =====================================================================
#  Loaders and Collectors
# =====================================================================

def load_complete_recursive(folder: Path, current_rel_path: Path = Path("")) -> FolderTree:
    """
    Crawls folder recursively. If it finds a database directory (containing event_group.json),
    loads the EventGroup. Otherwise, continues recursing into subdirectories.
    
    Returns a nested dictionary structure mirroring the folders, with EventGroup objects at the leaves.
    """
    tree: FolderTree = {}
    
    # Sort items for deterministic order
    for item in sorted(folder.iterdir(), key=lambda p: p.name):
        if item.is_dir():
            # Skip hidden folders (e.g. .git)
            if item.name.startswith('.'):
                continue
            
            rel_path = current_rel_path / item.name
            if is_eg_db(item):
                tree[item.name] = EventGroup.load_from_folder(item)
            else:
                subtree = load_complete_recursive(item, rel_path)
                if subtree:  # Only keep non-empty directories
                    tree[item.name] = subtree
                    
    return tree

def extract_event_groups(tree: FolderTree, current_path: Path = Path("")) -> list[CompleteEventGroup]:
    """
    Recursively flattens the recursive tree into a flat list of CompleteEventGroup wrappers.
    """
    eg_list: list[CompleteEventGroup] = []
    
    for name, val in tree.items():
        if isinstance(val, dict):
            eg_list.extend(extract_event_groups(val, current_path / name))
        elif isinstance(val, EventGroup):
            eg_list.append(CompleteEventGroup(
                event_group=val,
                db_name=name,
                db_path=current_path / name
            ))
            
    return eg_list

def extract_events(eg_list: list[CompleteEventGroup]) -> list[CompleteEvent]:
    """
    Extracts all events from the event groups into a flat list of CompleteEvent wrappers.
    """
    event_list: list[CompleteEvent] = []
    
    for ceg in eg_list:
        for event in ceg.event_group.events:
            event_list.append(CompleteEvent(
                event=event,
                event_group=ceg.event_group,
                db_name=ceg.db_name,
                db_path=ceg.db_path
            ))
            
    return event_list

def extract_circles(event_list: list[CompleteEvent]) -> list[CompleteCircle]:
    """
    Extracts all circles from the events into a flat list of CompleteCircle wrappers.
    """
    circle_list: list[CompleteCircle] = []
    
    for ce in event_list:
        if ce.event.circles:
            for circle in ce.event.circles:
                circle_list.append(CompleteCircle(
                    circle=circle,
                    event=ce.event,
                    event_group=ce.event_group,
                    db_name=ce.db_name,
                    db_path=ce.db_path
                ))
                
    return circle_list


def precompute_structures(db_root: Path) -> PrecomputedStructures:
    """
    Convenience function that crawls the database folder, loads all event groups,
    and extracts all flat and recursive core data structures into a PrecomputedStructures instance.
    """
    complete_recursive = load_complete_recursive(db_root)
    event_group_list = extract_event_groups(complete_recursive)
    event_list = extract_events(event_group_list)
    circle_list = extract_circles(event_list)
    
    return PrecomputedStructures(
        complete_recursive=complete_recursive,
        event_group_list=event_group_list,
        event_list=event_list,
        circle_list=circle_list
    )


# =====================================================================
#  Default Serializers
# =====================================================================

def default_serialize_event_group(ceg: CompleteEventGroup) -> SerializedEventGroup:
    """
    Standard JSON serialization for CompleteEventGroup.
    Includes all core fields and path metadata.
    """
    data = ceg.event_group.get_json()
    # Explicitly cast to SerializedEventGroup to satisfy type checkers
    out: SerializedEventGroup = {
        **data,  # type: ignore
        "db_name": ceg.db_name,
        "db_path": str(ceg.db_path.as_posix())
    }
    return out

def default_serialize_event(ce: CompleteEvent) -> SerializedEvent:
    """
    Standard JSON serialization for CompleteEvent.
    Includes event fields, path metadata, and parent group aliases.
    """
    data = ce.event.get_json()
    out: SerializedEvent = {
        **data,  # type: ignore
        "db_name": ce.db_name,
        "db_path": str(ce.db_path.as_posix()),
        "event_group_aliases": ce.event_group.aliases
    }
    return out

def default_serialize_circle(cc: CompleteCircle) -> SerializedCircle:
    """
    Standard JSON serialization for CompleteCircle.
    Includes circle fields, path metadata, and event/group aliases.
    """
    data = cc.circle.get_json()
    out: SerializedCircle = {
        **data,  # type: ignore
        "db_name": cc.db_name,
        "db_path": str(cc.db_path.as_posix()),
        "event_aliases": cc.event.aliases,
        "event_group_aliases": cc.event_group.aliases
    }
    return out


# =====================================================================
#  Serialization / Export Helpers
# =====================================================================

def serialize_recursive(
    tree: FolderTree,
    process_eg_fn: Callable[[EventGroup], Any] | None = None
) -> dict[str, Any]:
    """
    Recursively serializes the folder structure tree.
    Allows a custom process_eg_fn callback to format each EventGroup.
    If process_eg_fn is not provided, defaults to using EventGroup.get_json().
    """
    serialized: dict[str, Any] = {}
    
    for name, val in tree.items():
        if isinstance(val, dict):
            serialized[name] = serialize_recursive(val, process_eg_fn)
        elif isinstance(val, EventGroup):
            if process_eg_fn is not None:
                serialized[name] = process_eg_fn(val)
            else:
                serialized[name] = val.get_json()
                
    return serialized

def serialize_event_group_list(
    eg_list: list[CompleteEventGroup],
    process_eg_fn: Callable[[CompleteEventGroup], Any] | None = None
) -> list[Any]:
    """
    Serializes a list of CompleteEventGroups.
    Allows a custom process_eg_fn callback to format each CompleteEventGroup.
    If process_eg_fn is not provided, defaults to default_serialize_event_group.
    """
    fn = process_eg_fn if process_eg_fn is not None else default_serialize_event_group
    return [fn(ceg) for ceg in eg_list]

def serialize_event_list(
    event_list: list[CompleteEvent],
    process_event_fn: Callable[[CompleteEvent], Any] | None = None
) -> list[Any]:
    """
    Serializes a list of CompleteEvents.
    Allows a custom process_event_fn callback to format each CompleteEvent.
    If process_event_fn is not provided, defaults to default_serialize_event.
    """
    fn = process_event_fn if process_event_fn is not None else default_serialize_event
    return [fn(ce) for ce in event_list]

def serialize_circle_list(
    circle_list: list[CompleteCircle],
    process_circle_fn: Callable[[CompleteCircle], Any] | None = None
) -> list[Any]:
    """
    Serializes a list of CompleteCircles.
    Allows a custom process_circle_fn callback to format each CompleteCircle.
    If process_circle_fn is not provided, defaults to default_serialize_circle.
    """
    fn = process_circle_fn if process_circle_fn is not None else default_serialize_circle
    return [fn(cc) for cc in circle_list]
