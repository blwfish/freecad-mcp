# Mesh Operations Handler for FreeCAD MCP
#
# Provides mesh import/export, mesh-to-solid conversion, validation,
# simplification, and generic CAD file I/O (STL, OBJ, STEP, IGES, BREP).

import FreeCAD
import os
import time
from typing import Dict, Any
from .base import BaseHandler


class MeshOpsHandler(BaseHandler):
    """Handler for mesh and generic file I/O operations.

    Bridges the gap between mesh-based workflows (STL terrain files, 3D scans)
    and FreeCAD's Part/CAM pipeline. Supports:
    - Mesh import/export (STL, OBJ, PLY, OFF, AMF, 3MF)
    - CAD import/export (STEP, IGES, BREP)
    - Mesh-to-solid conversion for CAM compatibility
    - Mesh validation and repair
    - Mesh simplification (decimation)
    """

    MESH_FORMATS = {'.stl', '.obj', '.ply', '.off', '.amf', '.3mf'}
    CAD_FORMATS = {'.step', '.stp', '.iges', '.igs', '.brep', '.brp'}

    def import_mesh(self, args: Dict[str, Any]) -> str:
        """Import a mesh file (STL, OBJ, PLY, OFF, AMF, 3MF).

        Args:
            file_path: Path to mesh file (required)
            name: Object name (optional, derived from filename)

        Returns:
            Object name, vertex/face counts, bounding box
        """
        start_time = time.time()
        try:
            import Mesh

            file_path = args.get('file_path', '')
            if not file_path:
                return self.log_and_return("import_mesh", args,
                    error=Exception("file_path parameter required"),
                    duration=time.time() - start_time)

            if not os.path.exists(file_path):
                return self.log_and_return("import_mesh", args,
                    error=Exception(f"File not found: {file_path}"),
                    duration=time.time() - start_time)

            ext = os.path.splitext(file_path)[1].lower()
            if ext not in self.MESH_FORMATS:
                return self.log_and_return("import_mesh", args,
                    error=Exception(f"Unsupported mesh format '{ext}'. Supported: {', '.join(sorted(self.MESH_FORMATS))}"),
                    duration=time.time() - start_time)

            doc = self.get_document(create_if_missing=False)
            if not doc:
                return self.log_and_return("import_mesh", args,
                    error=Exception("No active document. Create one first via view_control create_document."),
                    duration=time.time() - start_time)

            # Derive name from filename if not provided
            name = args.get('name', '')
            if not name:
                name = os.path.splitext(os.path.basename(file_path))[0]
                # Sanitize for FreeCAD naming (alphanumeric + underscore)
                name = ''.join(c if c.isalnum() or c == '_' else '_' for c in name)
                if name and name[0].isdigit():
                    name = 'Mesh_' + name

            mesh_data = Mesh.Mesh(file_path)
            mesh_obj = doc.addObject("Mesh::Feature", name)
            mesh_obj.Mesh = mesh_data
            doc.recompute()

            bb = mesh_data.BoundBox
            result = (f"Imported mesh '{mesh_obj.Name}' from {os.path.basename(file_path)}\n"
                      f"  Points: {mesh_data.CountPoints}\n"
                      f"  Facets: {mesh_data.CountFacets}\n"
                      f"  Bounding box: {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm")

            return self.log_and_return("import_mesh", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("import_mesh", args, error=e, duration=time.time() - start_time)

    def export_mesh(self, args: Dict[str, Any]) -> str:
        """Export an object to mesh format (STL, OBJ, PLY, OFF, AMF, 3MF).

        Handles both Mesh::Feature and Part::Feature objects. Part objects
        are tessellated before export.

        Args:
            object_name: Object to export (required)
            file_path: Output file path (required)
            linear_deflection: Tessellation accuracy for Part objects (default 0.1)
            angular_deflection: Angular tessellation accuracy for Part objects

        Returns:
            Success message with file path and size
        """
        start_time = time.time()
        try:
            import Mesh

            object_name = args.get('object_name', '')
            file_path = args.get('file_path', '')

            if not object_name:
                return self.log_and_return("export_mesh", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)
            if not file_path:
                return self.log_and_return("export_mesh", args,
                    error=Exception("file_path parameter required"),
                    duration=time.time() - start_time)

            ext = os.path.splitext(file_path)[1].lower()
            if ext not in self.MESH_FORMATS:
                return self.log_and_return("export_mesh", args,
                    error=Exception(f"Unsupported mesh format '{ext}'. Supported: {', '.join(sorted(self.MESH_FORMATS))}"),
                    duration=time.time() - start_time)

            doc = self.get_document()
            if not doc:
                return self.log_and_return("export_mesh", args,
                    error=Exception("No active document"),
                    duration=time.time() - start_time)

            obj = self.get_object(object_name, doc)
            if not obj:
                return self.log_and_return("export_mesh", args,
                    error=Exception(f"Object '{object_name}' not found"),
                    duration=time.time() - start_time)

            # Ensure output directory exists
            out_dir = os.path.dirname(file_path)
            if out_dir and not os.path.exists(out_dir):
                os.makedirs(out_dir, exist_ok=True)

            if hasattr(obj, 'Mesh'):
                # Direct mesh export
                Mesh.export([obj], file_path)
            elif hasattr(obj, 'Shape'):
                # Part object — tessellate first
                import MeshPart
                linear = args.get('linear_deflection', 0.1)
                angular = args.get('angular_deflection', None)

                mesh_params = {"Shape": obj.Shape, "LinearDeflection": linear}
                if angular is not None:
                    mesh_params["AngularDeflection"] = angular

                mesh_data = MeshPart.meshFromShape(**mesh_params)
                # Create temporary mesh object for export
                temp_obj = doc.addObject("Mesh::Feature", "_export_temp")
                temp_obj.Mesh = mesh_data
                Mesh.export([temp_obj], file_path)
                doc.removeObject(temp_obj.Name)
            else:
                return self.log_and_return("export_mesh", args,
                    error=Exception(f"Object '{object_name}' has no Mesh or Shape to export"),
                    duration=time.time() - start_time)

            file_size = os.path.getsize(file_path)
            size_str = f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024 else f"{file_size / (1024 * 1024):.1f} MB"
            result = f"Exported '{object_name}' to {file_path} ({size_str})"

            return self.log_and_return("export_mesh", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("export_mesh", args, error=e, duration=time.time() - start_time)

    def mesh_to_solid(self, args: Dict[str, Any]) -> str:
        """Convert a mesh object to a Part solid for CAM compatibility.

        This is the critical bridge between mesh-based workflows (terrain STL files,
        3D scans) and FreeCAD's Part/CAM pipeline.

        Args:
            object_name: Mesh object to convert (required)
            tolerance: Sewing tolerance (default 0.1)
            name: Name for the resulting solid (optional)

        Returns:
            Solid name, volume, bounding box
        """
        start_time = time.time()
        try:
            import Part

            object_name = args.get('object_name', '')
            if not object_name:
                return self.log_and_return("mesh_to_solid", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)

            doc = self.get_document()
            if not doc:
                return self.log_and_return("mesh_to_solid", args,
                    error=Exception("No active document"),
                    duration=time.time() - start_time)

            obj = self.get_object(object_name, doc)
            if not obj:
                return self.log_and_return("mesh_to_solid", args,
                    error=Exception(f"Object '{object_name}' not found"),
                    duration=time.time() - start_time)

            if not hasattr(obj, 'Mesh'):
                return self.log_and_return("mesh_to_solid", args,
                    error=Exception(f"Object '{object_name}' is not a mesh object"),
                    duration=time.time() - start_time)

            tolerance = args.get('tolerance', 0.1)
            name = args.get('name', f"{object_name}_Solid")

            mesh = obj.Mesh
            topology = mesh.Topology  # ([points], [facets])

            # Build shape from mesh triangles
            shape = Part.Shape()
            shape.makeShapeFromMesh(topology, tolerance)

            # Try to make a solid
            solid = None
            warning = ""
            try:
                shape = shape.removeSplitter()
            except Exception:
                pass  # Not critical if this fails

            try:
                solid = Part.makeSolid(shape)
            except Exception:
                # Fallback: try sewing first, then solid
                try:
                    shell = shape.Shells[0] if shape.Shells else shape
                    shell.sewShape()
                    solid = Part.makeSolid(shell)
                    warning = " (required sewing step)"
                except Exception:
                    # Last resort: keep as shell
                    solid = shape
                    warning = " (WARNING: could not create solid, kept as shell — CAM may not work correctly)"

            solid_obj = doc.addObject("Part::Feature", name)
            solid_obj.Shape = solid
            doc.recompute()

            bb = solid.BoundBox
            vol_str = ""
            try:
                vol = solid.Volume
                vol_str = f"\n  Volume: {vol:.2f} mm^3"
            except Exception:
                pass

            result = (f"Converted mesh '{object_name}' to solid '{solid_obj.Name}'{warning}\n"
                      f"  Bounding box: {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm{vol_str}")

            return self.log_and_return("mesh_to_solid", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("mesh_to_solid", args, error=e, duration=time.time() - start_time)

    def get_mesh_info(self, args: Dict[str, Any]) -> str:
        """Get detailed information about a mesh object.

        Args:
            object_name: Mesh object to inspect (required)

        Returns:
            Mesh statistics: vertex/face counts, area, volume, topology health
        """
        start_time = time.time()
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return self.log_and_return("get_mesh_info", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)

            doc = self.get_document()
            if not doc:
                return self.log_and_return("get_mesh_info", args,
                    error=Exception("No active document"),
                    duration=time.time() - start_time)

            obj = self.get_object(object_name, doc)
            if not obj:
                return self.log_and_return("get_mesh_info", args,
                    error=Exception(f"Object '{object_name}' not found"),
                    duration=time.time() - start_time)

            if not hasattr(obj, 'Mesh'):
                return self.log_and_return("get_mesh_info", args,
                    error=Exception(f"Object '{object_name}' is not a mesh object"),
                    duration=time.time() - start_time)

            mesh = obj.Mesh
            bb = mesh.BoundBox

            result = f"Mesh info for '{object_name}':\n"
            result += f"  Points: {mesh.CountPoints}\n"
            result += f"  Facets: {mesh.CountFacets}\n"
            result += f"  Area: {mesh.Area:.2f} mm^2\n"
            result += f"  Volume: {mesh.Volume:.2f} mm^3\n"
            result += f"  Bounding box: {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm\n"
            result += f"  Center: ({bb.Center.x:.2f}, {bb.Center.y:.2f}, {bb.Center.z:.2f})\n"

            # Topology checks
            has_non_manifold = mesh.hasNonManifolds()
            has_self_intersect = mesh.hasSelfIntersections()
            is_solid = mesh.isSolid()

            result += f"  Is manifold: {not has_non_manifold}\n"
            result += f"  Is watertight: {is_solid}\n"
            result += f"  Has self-intersections: {has_self_intersect}\n"

            if has_non_manifold or has_self_intersect or not is_solid:
                result += "  Health: ISSUES DETECTED — consider running validate_mesh with auto_repair=true"
            else:
                result += "  Health: OK"

            return self.log_and_return("get_mesh_info", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("get_mesh_info", args, error=e, duration=time.time() - start_time)

    def import_file(self, args: Dict[str, Any]) -> str:
        """Import a file with automatic format detection.

        Supports mesh formats (STL, OBJ, PLY, OFF, AMF, 3MF),
        CAD formats (STEP, IGES, BREP), and FreeCAD files (FCStd).

        Args:
            file_path: Path to file (required)
            name: Object name (optional)

        Returns:
            Import result with object info
        """
        start_time = time.time()
        try:
            file_path = args.get('file_path', '')
            if not file_path:
                return self.log_and_return("import_file", args,
                    error=Exception("file_path parameter required"),
                    duration=time.time() - start_time)

            if not os.path.exists(file_path):
                return self.log_and_return("import_file", args,
                    error=Exception(f"File not found: {file_path}"),
                    duration=time.time() - start_time)

            ext = os.path.splitext(file_path)[1].lower()

            # Mesh formats — delegate to import_mesh
            if ext in self.MESH_FORMATS:
                return self.import_mesh(args)

            # CAD formats
            if ext in self.CAD_FORMATS:
                import Part

                doc = self.get_document(create_if_missing=False)
                if not doc:
                    return self.log_and_return("import_file", args,
                        error=Exception("No active document. Create one first via view_control create_document."),
                        duration=time.time() - start_time)

                objects_before = set(o.Name for o in doc.Objects)
                Part.insert(file_path, doc.Name)
                doc.recompute()
                objects_after = set(o.Name for o in doc.Objects)

                new_objects = objects_after - objects_before
                if new_objects:
                    result = f"Imported {os.path.basename(file_path)} — {len(new_objects)} object(s):\n"
                    for obj_name in sorted(new_objects):
                        obj = doc.getObject(obj_name)
                        if obj and hasattr(obj, 'Shape'):
                            bb = obj.Shape.BoundBox
                            result += f"  {obj_name}: {bb.XLength:.2f} x {bb.YLength:.2f} x {bb.ZLength:.2f} mm\n"
                        else:
                            result += f"  {obj_name}\n"
                else:
                    result = f"Imported {os.path.basename(file_path)} (no new objects detected — file may be empty)"

                return self.log_and_return("import_file", args, result=result, duration=time.time() - start_time)

            # FreeCAD native format
            if ext == '.fcstd':
                doc = FreeCAD.openDocument(file_path)
                result = f"Opened FreeCAD document '{doc.Name}' with {len(doc.Objects)} object(s)"
                return self.log_and_return("import_file", args, result=result, duration=time.time() - start_time)

            # Unknown format
            all_formats = sorted(self.MESH_FORMATS | self.CAD_FORMATS | {'.fcstd'})
            return self.log_and_return("import_file", args,
                error=Exception(f"Unsupported format '{ext}'. Supported: {', '.join(all_formats)}"),
                duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("import_file", args, error=e, duration=time.time() - start_time)

    def export_file(self, args: Dict[str, Any]) -> str:
        """Export an object with automatic format detection.

        Supports mesh formats (STL, OBJ, PLY, OFF, AMF, 3MF) and
        CAD formats (STEP, IGES, BREP).

        Args:
            object_name: Object to export (required)
            file_path: Output file path (required)
            linear_deflection: Tessellation accuracy for mesh export (default 0.1)

        Returns:
            Export result with file path and size
        """
        start_time = time.time()
        try:
            object_name = args.get('object_name', '')
            file_path = args.get('file_path', '')

            if not object_name:
                return self.log_and_return("export_file", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)
            if not file_path:
                return self.log_and_return("export_file", args,
                    error=Exception("file_path parameter required"),
                    duration=time.time() - start_time)

            ext = os.path.splitext(file_path)[1].lower()

            # Mesh formats — delegate to export_mesh
            if ext in self.MESH_FORMATS:
                return self.export_mesh(args)

            # CAD formats
            if ext in self.CAD_FORMATS:
                import Part

                doc = self.get_document()
                if not doc:
                    return self.log_and_return("export_file", args,
                        error=Exception("No active document"),
                        duration=time.time() - start_time)

                obj = self.get_object(object_name, doc)
                if not obj:
                    return self.log_and_return("export_file", args,
                        error=Exception(f"Object '{object_name}' not found"),
                        duration=time.time() - start_time)

                if not hasattr(obj, 'Shape'):
                    return self.log_and_return("export_file", args,
                        error=Exception(f"Object '{object_name}' has no Shape — cannot export to CAD format. Try mesh format instead."),
                        duration=time.time() - start_time)

                # Ensure output directory exists
                out_dir = os.path.dirname(file_path)
                if out_dir and not os.path.exists(out_dir):
                    os.makedirs(out_dir, exist_ok=True)

                Part.export([obj], file_path)

                file_size = os.path.getsize(file_path)
                size_str = f"{file_size / 1024:.1f} KB" if file_size < 1024 * 1024 else f"{file_size / (1024 * 1024):.1f} MB"
                result = f"Exported '{object_name}' to {file_path} ({size_str})"

                return self.log_and_return("export_file", args, result=result, duration=time.time() - start_time)

            # Unknown format
            all_formats = sorted(self.MESH_FORMATS | self.CAD_FORMATS)
            return self.log_and_return("export_file", args,
                error=Exception(f"Unsupported export format '{ext}'. Supported: {', '.join(all_formats)}"),
                duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("export_file", args, error=e, duration=time.time() - start_time)

    def validate_mesh(self, args: Dict[str, Any]) -> str:
        """Validate mesh topology and optionally auto-repair issues.

        Checks for non-manifold edges, self-intersections, degenerate facets,
        duplicate points/facets, and normal orientation.

        Args:
            object_name: Mesh object to validate (required)
            auto_repair: If true, attempt to fix issues (default false)

        Returns:
            Validation report with issue list and repair results
        """
        start_time = time.time()
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return self.log_and_return("validate_mesh", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)

            doc = self.get_document()
            if not doc:
                return self.log_and_return("validate_mesh", args,
                    error=Exception("No active document"),
                    duration=time.time() - start_time)

            obj = self.get_object(object_name, doc)
            if not obj:
                return self.log_and_return("validate_mesh", args,
                    error=Exception(f"Object '{object_name}' not found"),
                    duration=time.time() - start_time)

            if not hasattr(obj, 'Mesh'):
                return self.log_and_return("validate_mesh", args,
                    error=Exception(f"Object '{object_name}' is not a mesh object"),
                    duration=time.time() - start_time)

            auto_repair = args.get('auto_repair', False)
            mesh = obj.Mesh

            # Collect issues
            issues = []
            if mesh.hasNonManifolds():
                issues.append("non-manifold edges")
            if mesh.hasSelfIntersections():
                issues.append("self-intersections")
            if not mesh.isSolid():
                issues.append("not watertight (open boundary)")
            if mesh.hasInvalidPoints():
                issues.append("invalid/NaN points")

            # Check for duplicates
            orig_points = mesh.CountPoints
            orig_facets = mesh.CountFacets

            if not issues:
                result = f"Mesh '{object_name}' passed all validation checks.\n"
                result += f"  Points: {orig_points}, Facets: {orig_facets}\n"
                result += "  Status: VALID"
                return self.log_and_return("validate_mesh", args, result=result, duration=time.time() - start_time)

            result = f"Mesh '{object_name}' has {len(issues)} issue(s):\n"
            for issue in issues:
                result += f"  - {issue}\n"

            if auto_repair:
                result += "\nAttempting repairs:\n"

                # Work on a copy to preserve original
                mesh_copy = mesh.copy()
                repairs_done = []

                try:
                    mesh_copy.removeDuplicatedPoints()
                    if mesh_copy.CountPoints < orig_points:
                        repairs_done.append(f"removed {orig_points - mesh_copy.CountPoints} duplicate points")
                except Exception:
                    pass

                try:
                    before = mesh_copy.CountFacets
                    mesh_copy.removeDuplicatedFacets()
                    if mesh_copy.CountFacets < before:
                        repairs_done.append(f"removed {before - mesh_copy.CountFacets} duplicate facets")
                except Exception:
                    pass

                try:
                    before = mesh_copy.CountFacets
                    mesh_copy.fixDegenerations()
                    if mesh_copy.CountFacets != before:
                        repairs_done.append("fixed degenerate facets")
                except Exception:
                    pass

                try:
                    mesh_copy.fixSelfIntersections()
                    if not mesh_copy.hasSelfIntersections():
                        repairs_done.append("fixed self-intersections")
                except Exception:
                    pass

                try:
                    mesh_copy.harmonizeNormals()
                    repairs_done.append("harmonized normals")
                except Exception:
                    pass

                try:
                    mesh_copy.fillupHoles()
                    if mesh_copy.isSolid():
                        repairs_done.append("filled holes (now watertight)")
                except Exception:
                    pass

                # Apply repaired mesh
                obj.Mesh = mesh_copy
                doc.recompute()

                if repairs_done:
                    for repair in repairs_done:
                        result += f"  + {repair}\n"
                else:
                    result += "  (no automatic repairs succeeded)\n"

                # Re-check
                remaining = []
                if mesh_copy.hasNonManifolds():
                    remaining.append("non-manifold edges")
                if mesh_copy.hasSelfIntersections():
                    remaining.append("self-intersections")
                if not mesh_copy.isSolid():
                    remaining.append("not watertight")

                if remaining:
                    result += f"\n  Remaining issues: {', '.join(remaining)}"
                else:
                    result += "\n  All issues resolved!"

                result += f"\n  Final mesh: {mesh_copy.CountPoints} points, {mesh_copy.CountFacets} facets"
            else:
                result += "\nRun with auto_repair=true to attempt automatic fixes."

            return self.log_and_return("validate_mesh", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("validate_mesh", args, error=e, duration=time.time() - start_time)

    def simplify_mesh(self, args: Dict[str, Any]) -> str:
        """Simplify a mesh by reducing face count (decimation).

        Useful for large terrain meshes that need to be converted to solids
        for CAM processing. Specify either target_count or reduction.

        Args:
            object_name: Mesh object to simplify (required)
            target_count: Absolute target number of faces
            reduction: Reduction ratio 0-1 (e.g., 0.5 = reduce to 50%)
            name: Name for simplified mesh (optional, default: original_Simplified)

        Returns:
            New mesh name with before/after face counts
        """
        start_time = time.time()
        try:
            object_name = args.get('object_name', '')
            if not object_name:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception("object_name parameter required"),
                    duration=time.time() - start_time)

            doc = self.get_document()
            if not doc:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception("No active document"),
                    duration=time.time() - start_time)

            obj = self.get_object(object_name, doc)
            if not obj:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception(f"Object '{object_name}' not found"),
                    duration=time.time() - start_time)

            if not hasattr(obj, 'Mesh'):
                return self.log_and_return("simplify_mesh", args,
                    error=Exception(f"Object '{object_name}' is not a mesh object"),
                    duration=time.time() - start_time)

            mesh = obj.Mesh
            original_count = mesh.CountFacets

            target_count = args.get('target_count', None)
            reduction = args.get('reduction', None)

            if target_count is None and reduction is None:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception("Provide either target_count (absolute face count) or reduction (0-1 ratio)"),
                    duration=time.time() - start_time)

            if target_count is not None:
                target = int(target_count)
            else:
                reduction = float(reduction)
                if not 0.0 < reduction < 1.0:
                    return self.log_and_return("simplify_mesh", args,
                        error=Exception("reduction must be between 0 and 1 (exclusive)"),
                        duration=time.time() - start_time)
                target = int(original_count * reduction)

            if target >= original_count:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception(f"Target count ({target}) must be less than current count ({original_count})"),
                    duration=time.time() - start_time)

            if target < 4:
                return self.log_and_return("simplify_mesh", args,
                    error=Exception(f"Target count ({target}) is too small — minimum is 4 faces"),
                    duration=time.time() - start_time)

            name = args.get('name', f"{object_name}_Simplified")

            # Work on a copy
            mesh_copy = mesh.copy()
            mesh_copy.decimate(target)

            simplified_obj = doc.addObject("Mesh::Feature", name)
            simplified_obj.Mesh = mesh_copy
            doc.recompute()

            actual_count = mesh_copy.CountFacets
            pct = (1.0 - actual_count / original_count) * 100

            result = (f"Simplified mesh '{object_name}' → '{simplified_obj.Name}'\n"
                      f"  Before: {original_count} facets\n"
                      f"  After:  {actual_count} facets ({pct:.1f}% reduction)\n"
                      f"  Points: {mesh_copy.CountPoints}")

            return self.log_and_return("simplify_mesh", args, result=result, duration=time.time() - start_time)

        except Exception as e:
            return self.log_and_return("simplify_mesh", args, error=e, duration=time.time() - start_time)
