import itertools as it
import re
import string
from typing import Dict, List
import warnings
import os
import hashlib

from xml.dom import minidom
from xml.dom.minidom import Element as MinidomElement

from manimlib.constants import DEFAULT_STROKE_WIDTH
from manimlib.constants import ORIGIN, UP, DOWN, LEFT, RIGHT
from manimlib.constants import BLACK
from manimlib.constants import WHITE

from .style_utils import cascade_element_style, parse_style
from manimlib.mobject.geometry import Circle
from manimlib.mobject.geometry import Rectangle
from manimlib.mobject.geometry import RoundedRectangle
from manimlib.mobject.types.vectorized_mobject import VGroup
from manimlib.mobject.types.vectorized_mobject import VMobject
from manimlib.utils.color import *
from manimlib.utils.config_ops import digest_config
from manimlib.utils.directories import get_mobject_data_dir, get_vector_image_dir
from manimlib.utils.images import get_full_vector_image_path


def string_to_numbers(num_string):
    num_string = num_string.replace("-", ",-")
    num_string = num_string.replace("e,-", "e-")
    return [float(s) for s in re.split("[ ,]", num_string) if s != ""]


class SVGMobject(VMobject):
    CONFIG = {
        "should_center": True,
        "height": 2,
        "width": None,
        # Must be filled in in a subclass, or when called
        "file_name": None,
        "unpack_groups": True,  # if False, creates a hierarchy of VGroups
        # TODO, style components should be read in, not defaulted
        "stroke_width": DEFAULT_STROKE_WIDTH,
        "fill_opacity": 1.0,
        "path_string_config": {},
    }

    def __init__(self, file_name=None, **kwargs):
        digest_config(self, kwargs)
        self.def_map = {}
        self.file_name = file_name or self.file_name
        self.ensure_valid_file()
        if file_name is None:
            raise Exception("Must specify file for SVGMobject")
        self.file_path = get_full_vector_image_path(file_name)

        super().__init__(**kwargs)
        self.move_into_position()

    def ensure_valid_file(self):
        """Reads self.file_name and determines whether the given input file_name
        is valid.
        """
        if self.file_name is None:
            raise Exception("Must specify file for SVGMobject")

        if os.path.exists(self.file_name):
            self.file_path = self.file_name
            return

        relative = os.path.join(os.getcwd(), self.file_name)
        if os.path.exists(relative):
            self.file_path = relative
            return

        possible_paths = [
            os.path.join(get_vector_image_dir(), self.file_name),
            os.path.join(get_vector_image_dir(), self.file_name + ".xdv"),
            os.path.join(get_vector_image_dir(), self.file_name + ".svg"),
            self.file_name,
            self.file_name + ".svg",
            self.file_name + ".xdv",
        ]
        for path in possible_paths:
            if os.path.exists(path):
                self.file_path = path
                return
        error = f"From: {os.getcwd()}, could not find {self.file_name} at either of these locations: {possible_paths}"
        raise IOError(error)

    def move_into_position(self):
        if self.should_center:
            self.center()
        if self.height is not None:
            self.set_height(self.height)
        if self.width is not None:
            self.set_width(self.width)

    def init_points(self):
        """Called by the Mobject abstract base class. Responsible for generating
        the SVGMobject's points from XML tags, populating self.mobjects, and
        any submobjects within self.mobjects.
        """
        doc = minidom.parse(self.file_path)
        for svg in doc.getElementsByTagName("svg"):
            mobjects = self.get_mobjects_from(svg, {})
            if self.unpack_groups:
                self.add(*mobjects)
            else:
                self.add(*mobjects[0].submobjects)
        doc.unlink()

    def get_mobjects_from(
        self,
        element: MinidomElement,
        inherited_style: Dict[str, str],
        within_defs: bool = False,
    ) -> List[VMobject]:
        """Parses a given SVG element into a Mobject.

        Parameters
        ----------
        element : :class:`Element`
            The SVG data in the XML to be parsed.

        inherited_style : :class:`dict`
            Dictionary of the SVG attributes for children to inherit.

        within_defs : :class:`bool`
            Whether ``element`` is within a ``defs`` element, which indicates
            whether elements with `id` attributes should be added to the
            definitions list.

        Returns
        -------
        List[VMobject]
            A VMobject representing the associated SVG element.
        """
        result = []
        # First, let all non-elements pass (like text entries)
        if not isinstance(element, MinidomElement):
            return result

        style = cascade_element_style(element, inherited_style)
        is_defs = element.tagName == "defs"

        if element.tagName == "style":
            pass  # TODO, handle style
        elif element.tagName in ["g", "svg", "symbol", "defs"]:
            result += it.chain(
                *[
                    self.get_mobjects_from(
                        child, style, within_defs=within_defs or is_defs
                    )
                    for child in element.childNodes
                ]
            )
        elif element.tagName == "path":
            temp = element.getAttribute("d")
            if temp != "":
                result.append(self.path_string_to_mobject(temp, style))
        elif element.tagName == "use":
            # note, style is calcuated in a different way for `use` elements.
            result += self.use_to_mobjects(element, style)
        elif element.tagName == "rect":
            result.append(self.rect_to_mobject(element, style))
        elif element.tagName == "circle":
            result.append(self.circle_to_mobject(element, style))
        elif element.tagName == "ellipse":
            result.append(self.ellipse_to_mobject(element, style))
        elif element.tagName in ["polygon", "polyline"]:
            result.append(self.polygon_to_mobject(element, style))
        else:
            pass  # TODO

        result = [m for m in result if m is not None]
        self.handle_transforms(element, VGroup(*result))
        if len(result) > 1 and not self.unpack_groups:
            result = [VGroup(*result)]

        if within_defs and element.hasAttribute("id"):
            # it seems wasteful to throw away the actual element,
            # but I'd like the parsing to be as similar as possible
            self.def_map[element.getAttribute("id")] = (style, element)
        if is_defs:
            # defs shouldn't be part of the result tree, only the id dictionary.
            return []

        return result

    def path_string_to_mobject(self, path_string: str, style: dict):
        """Converts a SVG path element's ``d`` attribute to a mobject.

        Parameters
        ----------
        path_string : :class:`str`
            A path with potentially multiple path commands to create a shape.

        style : :class:`dict`
            Style specification, using the SVG names for properties.

        Returns
        -------
        VMobjectFromSVGPathstring
            A VMobject from the given path string, or d attribute.
        """
        return VMobjectFromSVGPathstring(path_string, **parse_style(style))

    def use_to_mobjects(
        self, use_element: MinidomElement, local_style: Dict
    ) -> List[VMobject]:
        """Converts a SVG <use> element to a collection of VMobjects.

        Parameters
        ----------
        use_element : :class:`MinidomElement`
            An SVG <use> element which represents nodes that should be
            duplicated elsewhere.

        local_style : :class:`Dict`
            The styling using SVG property names at the point the element is `<use>`d.
            Not all values are applied; styles defined when the element is specified in
            the `<def>` tag cannot be overriden here.

        Returns
        -------
        List[VMobject]
            A collection of VMobjects that are a copy of the defined object
        """

        # Remove initial "#" character
        ref = use_element.getAttribute("xlink:href")[1:]

        try:
            def_style, def_element = self.def_map[ref]
        except KeyError:
            warning_text = f"{self.file_name} contains a reference to id #{ref}, which is not recognized"
            warnings.warn(warning_text)
            return []

        # In short, the def-ed style overrides the new style,
        # in cases when the def-ed styled is defined.
        style = local_style.copy()
        style.update(def_style)

        return self.get_mobjects_from(def_element, style)

    def attribute_to_float(self, attr):
        stripped_attr = "".join(
            [char for char in attr if char in string.digits + "." + "-"]
        )
        return float(stripped_attr)

    def polygon_to_mobject(self, polygon_element: MinidomElement, style: dict):
        """Constructs a VMobject from a SVG <polygon> element.

        Parameters
        ----------
        polygon_element : :class:`minidom.Element`
            An SVG polygon element.

        style : :class:`dict`
            Style specification, using the SVG names for properties.

        Returns
        -------
        VMobjectFromSVGPathstring
            A VMobject representing the polygon.
        """
        # This seems hacky... yes it is.
        path_string = polygon_element.getAttribute("points").lstrip()
        for digit in string.digits:
            path_string = path_string.replace(" " + digit, " L" + digit)
        path_string = "M" + path_string
        if polygon_element.tagName == "polygon":
            path_string = path_string + "Z"
        return self.path_string_to_mobject(path_string, style)

    def circle_to_mobject(self, circle_element: MinidomElement, style: dict):
        """Creates a Circle VMobject from a SVG <circle> command.

        Parameters
        ----------
        circle_element : :class:`minidom.Element`
            A SVG circle path command.

        style : :class:`dict`
            Style specification, using the SVG names for properties.

        Returns
        -------
        Circle
            A Circle VMobject
        """
        x, y, r = [
            self.attribute_to_float(circle_element.getAttribute(key))
            if circle_element.hasAttribute(key)
            else 0.0
            for key in ("cx", "cy", "r")
        ]
        return Circle(radius=r, **parse_style(style)).shift(x * RIGHT + y * DOWN)

    def ellipse_to_mobject(self, circle_element: MinidomElement, style: dict):
        """Creates a stretched Circle VMobject from a SVG <circle> path
        command.

        Parameters
        ----------
        circle_element : :class:`minidom.Element`
            A SVG circle path command.

        style : :class:`dict`
            Style specification, using the SVG names for properties.

        Returns
        -------
        Circle
            A Circle VMobject
        """
        x, y, rx, ry = [
            self.attribute_to_float(circle_element.getAttribute(key))
            if circle_element.hasAttribute(key)
            else 0.0
            for key in ("cx", "cy", "rx", "ry")
        ]
        return (
            Circle(**parse_style(style))
            .scale(rx * RIGHT + ry * UP)
            .shift(x * RIGHT + y * DOWN)
        )

    def rect_to_mobject(self, rect_element: MinidomElement, style: dict):
        """Converts a SVG <rect> command to a VMobject.

        Parameters
        ----------
        rect_element : minidom.Element
            A SVG rect path command.

        style : dict
            Style specification, using the SVG names for properties.

        Returns
        -------
        Rectangle
            Creates either a Rectangle, or RoundRectangle, VMobject from a
            rect element.
        """

        stroke_width = rect_element.getAttribute("stroke-width")
        corner_radius = rect_element.getAttribute("rx")

        if stroke_width in ["", "none", "0"]:
            stroke_width = 0

        if corner_radius in ["", "0", "none"]:
            corner_radius = 0

        corner_radius = float(corner_radius)

        parsed_style = parse_style(style)
        parsed_style["stroke_width"] = stroke_width

        if corner_radius == 0:
            mob = Rectangle(
                width=self.attribute_to_float(rect_element.getAttribute("width")),
                height=self.attribute_to_float(rect_element.getAttribute("height")),
                **parsed_style,
            )
        else:
            mob = RoundedRectangle(
                width=self.attribute_to_float(rect_element.getAttribute("width")),
                height=self.attribute_to_float(rect_element.getAttribute("height")),
                corner_radius=corner_radius,
                **parsed_style,
            )

        mob.shift(mob.get_center() - mob.get_corner(UP + LEFT))
        return mob

    def handle_transforms(self, element, mobject):
        """Applies the SVG transform to the specified mobject. Transforms include:
        ``matrix``, ``translate``, and ``scale``.

        Parameters
        ----------
        element : :class:`minidom.Element`
            The transform command to perform

        mobject : :class:`Mobject`
            The Mobject to transform.
        """

        if element.hasAttribute("x") and element.hasAttribute("y"):
            x = self.attribute_to_float(element.getAttribute("x"))
            # Flip y
            y = -self.attribute_to_float(element.getAttribute("y"))
            mobject.shift(x * RIGHT + y * UP)

        transform_attr_value = element.getAttribute("transform")

        # parse the various transforms in the attribute value
        transform_names = ["matrix", "translate", "scale", "rotate", "skewX", "skewY"]

        # Borrowed/Inspired from:
        # https://github.com/cjlano/svg/blob/3ea3384457c9780fa7d67837c9c5fd4ebc42cb3b/svg/svg.py#L75

        # match any SVG transformation with its parameter (until final parenthese)
        # [^)]*    == anything but a closing parenthese
        # '|'.join == OR-list of SVG transformations
        transform_regex = "|".join([x + r"[^)]*\)" for x in transform_names])
        transforms = re.findall(transform_regex, transform_attr_value)

        number_regex = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"

        for t in transforms:
            op_name, op_args = t.split("(")
            op_name = op_name.strip()
            op_args = [float(x) for x in re.findall(number_regex, op_args)]

            if op_name == "matrix":
                transform_args = np.array(op_args).reshape([3, 2])
                x = transform_args[2][0]
                y = -transform_args[2][1]
                matrix = np.identity(self.dim)
                matrix[:2, :2] = transform_args[:2, :]
                matrix[1] *= -1
                matrix[:, 1] *= -1

                for mob in mobject.family_members_with_points():
                    mob.points = np.dot(mob.points, matrix)
                mobject.shift(x * RIGHT + y * UP)

            elif op_name == "scale":
                scale_values = op_args
                if len(scale_values) == 2:
                    scale_x, scale_y = scale_values
                    mobject.scale(np.array([scale_x, scale_y, 1]), about_point=ORIGIN)
                elif len(scale_values) == 1:
                    scale = scale_values[0]
                    mobject.scale(np.array([scale, scale, 1]), about_point=ORIGIN)

            elif op_name == "translate":
                if len(op_args) == 2:
                    x, y = op_args
                else:
                    x = op_args
                    y = 0
                mobject.shift(x * RIGHT + y * DOWN)

            else:
                pass
                # TODO: handle rotate, skewX and skewY
                # for now adding a warning message

    def flatten(self, input_list):
        """A helper method to flatten the ``input_list`` into an 1D array."""
        output_list = []
        for i in input_list:
            if isinstance(i, list):
                output_list.extend(self.flatten(i))
            else:
                output_list.append(i)
        return output_list

    def get_all_childNodes_have_id(self, element):
        all_childNodes_have_id = []
        if not isinstance(element, minidom.Element):
            return
        if element.hasAttribute("id"):
            return [element]
        for e in element.childNodes:
            all_childNodes_have_id.append(self.get_all_childNodes_have_id(e))
        return self.flatten([e for e in all_childNodes_have_id if e])


class VMobjectFromSVGPathstring(VMobject):
    CONFIG = {
        "long_lines": True,
        "should_subdivide_sharp_curves": False,
        "should_remove_null_curves": False,
    }

    def __init__(self, path_string, **kwargs):
        self.path_string = path_string
        super().__init__(**kwargs)

    def init_points(self):
        # After a given svg_path has been converted into points, the result
        # will be saved to a file so that future calls for the same path
        # don't need to retrace the same computation.
        hasher = hashlib.sha256(self.path_string.encode())
        path_hash = hasher.hexdigest()[:16]
        points_filepath = os.path.join(
            get_mobject_data_dir(), f"{path_hash}_points.npy"
        )
        tris_filepath = os.path.join(get_mobject_data_dir(), f"{path_hash}_tris.npy")

        if os.path.exists(points_filepath) and os.path.exists(tris_filepath):
            self.set_points(np.load(points_filepath))
        else:
            self.relative_point = np.array(ORIGIN)
            for command, coord_string in self.get_commands_and_coord_strings():
                new_points = self.string_to_points(command, coord_string)
                self.handle_command(command, new_points)
            if self.should_subdivide_sharp_curves:
                # For a healthy triangulation later
                self.subdivide_sharp_curves()
            if self.should_remove_null_curves:
                # Get rid of any null curves
                self.set_points(self.get_points_without_null_curves())
            # SVG treats y-coordinate differently
            self.stretch(-1, 1, about_point=ORIGIN)
            # Save to a file for future use
            np.save(points_filepath, self.get_points())

    def get_commands_and_coord_strings(self):
        all_commands = list(self.get_command_to_function_map().keys())
        all_commands += [c.lower() for c in all_commands]
        pattern = "[{}]".format("".join(all_commands))
        return zip(
            re.findall(pattern, self.path_string),
            re.split(pattern, self.path_string)[1:],
        )

    def handle_command(self, command, new_points):
        if command.islower():
            # Treat it as a relative command
            new_points += self.relative_point

        func, n_points = self.command_to_function(command)
        func(*new_points[:n_points])
        leftover_points = new_points[n_points:]

        # Recursively handle the rest of the points
        if len(leftover_points) > 0:
            if command.upper() == "M":
                # Treat following points as relative line coordinates
                command = "l"
            if command.islower():
                leftover_points -= self.relative_point
                self.relative_point = self.get_last_point()
            self.handle_command(command, leftover_points)
        else:
            # Command is over, reset for future relative commands
            self.relative_point = self.get_last_point()

    def string_to_points(self, command, coord_string):
        numbers = string_to_numbers(coord_string)
        if command.upper() in ["H", "V"]:
            i = {"H": 0, "V": 1}[command.upper()]
            xy = np.zeros((len(numbers), 2))
            xy[:, i] = numbers
            if command.isupper():
                xy[:, 1 - i] = self.relative_point[1 - i]
        elif command.upper() == "A":
            raise Exception("Not implemented")
        else:
            xy = np.array(numbers).reshape((len(numbers) // 2, 2))
        result = np.zeros((xy.shape[0], self.dim))
        result[:, :2] = xy
        return result

    def command_to_function(self, command):
        return self.get_command_to_function_map()[command.upper()]

    def get_command_to_function_map(self):
        """
        Associates svg command to VMobject function, and
        the number of arguments it takes in
        """
        return {
            "M": (self.start_new_path, 1),
            "L": (self.add_line_to, 1),
            "H": (self.add_line_to, 1),
            "V": (self.add_line_to, 1),
            "C": (self.add_cubic_bezier_curve_to, 3),
            "S": (self.add_smooth_cubic_curve_to, 2),
            "Q": (self.add_quadratic_bezier_curve_to, 2),
            "T": (self.add_smooth_curve_to, 1),
            "A": (self.add_quadratic_bezier_curve_to, 2),  # TODO
            "Z": (self.close_path, 0),
        }

    def get_original_path_string(self):
        return self.path_string
