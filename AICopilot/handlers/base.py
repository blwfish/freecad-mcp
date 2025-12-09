# Base handler class for FreeCAD MCP operations

import FreeCAD
import FreeCADGui
from typing import Dict, Any


class BaseHandler:
    """Base class for all FreeCAD operation handlers.

    Provides common utilities and document access patterns.
    """

    def __init__(self, server=None):
        """Initialize handler with optional reference to server.

        Args:
            server: Reference to FreeCADSocketServer for accessing shared resources
                   like selector, gui_task_queue, etc.
        """
        self.server = server

    @property
    def selector(self):
        """Access the selection manager from the server."""
        return self.server.selector if self.server else None

    def get_document(self, create_if_missing: bool = False) -> FreeCAD.Document:
        """Get active document, optionally creating one if missing.

        Args:
            create_if_missing: If True, create a new document if none exists

        Returns:
            Active FreeCAD document or None
        """
        doc = FreeCAD.ActiveDocument
        if not doc and create_if_missing:
            doc = FreeCAD.newDocument()
        return doc

    def get_object(self, object_name: str, doc: FreeCAD.Document = None):
        """Get an object by name from the document.

        Args:
            object_name: Name of the object to find
            doc: Document to search in (uses active document if not specified)

        Returns:
            FreeCAD object or None if not found
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        return doc.getObject(object_name)

    def recompute(self, doc: FreeCAD.Document = None):
        """Recompute the document.

        Args:
            doc: Document to recompute (uses active document if not specified)
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc:
            doc.recompute()

    def find_body(self, doc: FreeCAD.Document = None):
        """Find a PartDesign Body in the document.

        Args:
            doc: Document to search (uses active document if not specified)

        Returns:
            First PartDesign::Body found, or None
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        for obj in doc.Objects:
            if obj.TypeId == "PartDesign::Body":
                return obj
        return None

    def find_body_for_object(self, obj, doc: FreeCAD.Document = None):
        """Find the PartDesign Body containing an object.

        Args:
            obj: Object to find the body for
            doc: Document to search (uses active document if not specified)

        Returns:
            PartDesign::Body containing the object, or None
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            return None
        for body in doc.Objects:
            if body.TypeId == "PartDesign::Body" and obj in body.Group:
                return body
        return None

    def create_body_if_needed(self, doc: FreeCAD.Document = None):
        """Create a PartDesign Body if one doesn't exist.

        Args:
            doc: Document to create body in (uses active document if not specified)

        Returns:
            Existing or newly created PartDesign::Body
        """
        if doc is None:
            doc = FreeCAD.ActiveDocument
        if doc is None:
            doc = FreeCAD.newDocument()

        body = self.find_body(doc)
        if not body:
            body = doc.addObject("PartDesign::Body", "Body")
            doc.recompute()
        return body
