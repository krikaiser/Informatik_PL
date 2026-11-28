"""Orthogonale Vorschau der Geometrie aus dem geprüften IFC-Modell."""

from io import BytesIO

import ifcopenshell.geom
import numpy as np
from ifcopenshell.util.shape import get_edges, get_faces, get_shape_vertices
from PIL import Image, ImageDraw

from ifc_pset_check import Audit, open_ifc


def render_model_preview(audit: Audit) -> BytesIO:
    model = open_ifc(audit.path, expected_stamp=audit.file_stamp)
    products = [product for product in model.by_type("IfcProduct") if product.Representation is not None]
    visible = [
        product for product in products
        if not any(product.is_a(name) for name in ("IfcFeatureElementSubtraction", "IfcSpace", "IfcAnnotation"))
    ]
    if not products:
        raise ValueError("Die IFC-Datei enthält keine Produktgeometrie.")

    settings = ifcopenshell.geom.settings()
    # Lokale Geometrie lässt sich für wiederholte Bauteile wiederverwenden.
    # Die Übersichtsansicht benötigt keine erneuten Booleschen Öffnungsschnitte.
    settings.set("disable-opening-subtractions", True)
    meshes = model_meshes(model, visible or products, settings)
    if not meshes:
        settings.set("dimensionality", ifcopenshell.ifcopenshell_wrapper.CURVES_SURFACES_AND_SOLIDS)
        meshes = model_meshes(model, products, settings)
    if not meshes:
        raise ValueError("Die IFC-Datei enthält keine darstellbare Produktgeometrie.")

    return draw_preview(meshes)


def model_meshes(model, products, settings):
    meshes = []
    for shape in ifcopenshell.geom.iterate(
        settings, model, include=products, geometry_library="hybrid-cgal-simple-opencascade",
    ):
        vertices = get_shape_vertices(shape, shape.geometry)
        faces = get_faces(shape.geometry)
        edges = get_edges(shape.geometry) if not len(faces) else None
        if len(vertices) and np.isfinite(vertices).all() and (len(faces) or len(edges)):
            meshes.append((vertices, faces, edges))
    return meshes


def draw_preview(meshes) -> BytesIO:
    points = np.concatenate([vertices for vertices, _, _ in meshes])
    centre = (points.min(axis=0) + points.max(axis=0)) / 2
    horizontal = points[:, :2] - centre[:2]
    _, axes = np.linalg.eigh(horizontal.T @ horizontal)
    direction = axes[:, -1]
    if direction[np.argmax(np.abs(direction))] < 0:
        direction = -direction
    right = np.r_[direction, 0.0]
    across = np.array([-right[1], right[0], 0.0])
    vertical = np.array([0.0, 0.0, 1.0])
    elevation = np.deg2rad(56)
    up = np.sin(elevation) * across + np.cos(elevation) * vertical
    depth = np.cross(right, up)
    camera = np.column_stack((right, up, depth))

    width, height = 1560, 540
    view_points = (points - centre) @ camera
    low = view_points[:, :2].min(axis=0)
    high = view_points[:, :2].max(axis=0)
    span = high - low
    scale = min((width - 90) / max(span[0], 1e-9), (height - 80) / max(span[1], 1e-9))
    projected = np.rint((view_points[:, :2] - (low + high) / 2) * (scale, -scale) + (width / 2, height / 2))
    projected = projected.astype(np.int32)

    triangles, shades, depths, lines = [], [], [], []
    light = 0.38 * right + 0.84 * vertical - 0.38 * across
    offset = 0
    for vertices, faces, edges in meshes:
        if len(faces):
            indexes = offset + faces
            screen = projected[indexes]
            edge1 = screen[:, 1] - screen[:, 0]
            edge2 = screen[:, 2] - screen[:, 0]
            drawable = edge1[:, 0] * edge2[:, 1] != edge1[:, 1] * edge2[:, 0]
            faces, indexes = faces[drawable], indexes[drawable]
            face_points = vertices[faces]
            normals = np.cross(face_points[:, 1] - face_points[:, 0], face_points[:, 2] - face_points[:, 0])
            brightness = np.abs(normals @ light) / np.maximum(np.linalg.norm(normals, axis=1), 1e-12)
            palette = np.array([92, 117, 134]) + brightness[:, None] * (np.array([118, 108, 96]))
            shades.append(np.clip(palette, 0, 255).astype(np.uint8))
            triangles.append(projected[indexes])
            depths.append(view_points[indexes, 2].mean(axis=1))
        elif edges is not None:
            lines.extend(projected[offset + edges].reshape(-1, 4).tolist())
        offset += len(vertices)

    image = Image.new("RGB", (width, height), "#eef3f6")
    draw = ImageDraw.Draw(image)
    if triangles:
        order = np.argsort(np.concatenate(depths))
        faces = np.concatenate(triangles)[order].reshape(-1, 6).tolist()
        colours = np.concatenate(shades)[order].tolist()
        for coordinates, colour in zip(faces, colours, strict=True):
            draw.polygon(coordinates, fill=tuple(colour))
    for line in lines:
        draw.line(line, fill="#526e80", width=2)
    if not lines and not any(len(mesh) for mesh in triangles):
        raise ValueError("Die Modellansicht enthält keine sichtbare Geometrie.")
    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output
