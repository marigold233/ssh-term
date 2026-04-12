use pyo3::prelude::*;
use std::collections::VecDeque;
use vte::{Params, Parser, Perform};

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Color {
    Default,
    Named(&'static str),
    Indexed(u8),
    Rgb(u8, u8, u8),
}

impl Color {
    fn to_string_repr(&self) -> String {
        match self {
            Color::Default => "default".to_string(),
            Color::Named(name) => name.to_string(),
            Color::Indexed(idx) => format!("color{}", idx),
            Color::Rgb(r, g, b) => format!("#{:02x}{:02x}{:02x}", r, g, b),
        }
    }
}

#[pyclass]
#[derive(Clone, Copy)]
pub struct PyChar {
    #[pyo3(get)]
    pub data: char,
    // fg and bg are internal only since we use segments at the python layer
    pub fg: Color,
    pub bg: Color,
    #[pyo3(get)]
    pub bold: bool,
    #[pyo3(get)]
    pub italics: bool,
    #[pyo3(get)]
    pub underscore: bool,
    #[pyo3(get)]
    pub inverse: bool,
}

impl Default for PyChar {
    fn default() -> Self {
        PyChar {
            data: ' ',
            fg: Color::Default,
            bg: Color::Default,
            bold: false,
            italics: false,
            underscore: false,
            inverse: false,
        }
    }
}

#[pyclass]
#[derive(Clone)]
pub struct PyCursor {
    #[pyo3(get, set)]
    pub x: usize,
    #[pyo3(get, set)]
    pub y: usize,
}

#[derive(Clone, Copy)]
struct CurrentStyle {
    fg: Color,
    bg: Color,
    bold: bool,
    italics: bool,
    underscore: bool,
    inverse: bool,
}

impl Default for CurrentStyle {
    fn default() -> Self {
        CurrentStyle {
            fg: Color::Default,
            bg: Color::Default,
            bold: false,
            italics: false,
            underscore: false,
            inverse: false,
        }
    }
}

#[pyclass]
pub struct Screen {
    #[pyo3(get)]
    pub columns: usize,
    #[pyo3(get)]
    pub lines: usize,
    #[pyo3(get)]
    pub cursor: PyCursor,
    buffer: VecDeque<Vec<PyChar>>,
    scrollback_buffer: VecDeque<Vec<PyChar>>,
    current_style: CurrentStyle,
    margins: Option<(usize, usize)>,
}

#[pymethods]
impl Screen {
    #[new]
    pub fn new(columns: usize, lines: usize) -> Self {
        let columns = std::cmp::max(1, columns);
        let lines = std::cmp::max(1, lines);
        let mut buffer = VecDeque::with_capacity(lines);
        for _ in 0..lines {
            buffer.push_back(vec![PyChar::default(); columns]);
        }
        Screen {
            columns,
            lines,
            cursor: PyCursor { x: 0, y: 0 },
            buffer,
            scrollback_buffer: VecDeque::new(),
            current_style: CurrentStyle::default(),
            margins: None,
        }
    }

    pub fn resize(&mut self, lines: usize, columns: usize) {
        let lines = std::cmp::max(1, lines);
        let columns = std::cmp::max(1, columns);
        self.lines = lines;
        self.columns = columns;
        self.buffer.resize_with(lines, || vec![PyChar::default(); columns]);
        for row in self.buffer.iter_mut() {
            row.resize(columns, PyChar::default());
        }
        if self.cursor.y >= lines {
            self.cursor.y = lines.saturating_sub(1);
        }
        if self.cursor.x >= columns {
            self.cursor.x = columns.saturating_sub(1);
        }
        self.margins = None;
    }

    pub fn get_total_lines(&self) -> usize {
        self.scrollback_buffer.len() + self.lines
    }

    pub fn get_line_segments(&self, y: usize) -> Vec<(String, String, String, bool, bool, bool, bool, bool)> {
        let mut segments = Vec::new();
        let total = self.scrollback_buffer.len() + self.lines;
        if y >= total {
            return segments;
        }
        
        let in_history = y < self.scrollback_buffer.len();
        let row = if in_history {
            &self.scrollback_buffer[y]
        } else {
            let buffer_y = y - self.scrollback_buffer.len();
            if buffer_y < self.buffer.len() {
                &self.buffer[buffer_y]
            } else {
                return segments;
            }
        };

        if row.is_empty() {
            return segments;
        }

        let mut current_text = String::new();
        let mut current_style: Option<(Color, Color, bool, bool, bool, bool, bool)> = None;

        for (x, cell) in row.iter().enumerate() {
            let is_cursor = !in_history && (y - self.scrollback_buffer.len()) == self.cursor.y && x == self.cursor.x;
            
            let style = (cell.fg, cell.bg, cell.bold, cell.italics, cell.underscore, cell.inverse, is_cursor);

            if let Some(cs) = current_style {
                if cs == style {
                    current_text.push(cell.data);
                } else {
                    segments.push((
                        current_text.clone(),
                        cs.0.to_string_repr(),
                        cs.1.to_string_repr(),
                        cs.2,
                        cs.3,
                        cs.4,
                        cs.5,
                        cs.6,
                    ));
                    current_text.clear();
                    current_text.push(cell.data);
                    current_style = Some(style);
                }
            } else {
                current_text.push(cell.data);
                current_style = Some(style);
            }
        }
        
        if let Some(cs) = current_style {
            if !current_text.is_empty() {
                segments.push((
                    current_text,
                    cs.0.to_string_repr(),
                    cs.1.to_string_repr(),
                    cs.2,
                    cs.3,
                    cs.4,
                    cs.5,
                    cs.6,
                ));
            }
        }
        segments
    }
}

impl Screen {
    fn blank_char(&self) -> PyChar {
        PyChar {
            data: ' ',
            fg: self.current_style.fg,
            bg: self.current_style.bg,
            bold: self.current_style.bold,
            italics: self.current_style.italics,
            underscore: self.current_style.underscore,
            inverse: self.current_style.inverse,
        }
    }

    fn get_margins(&self) -> (usize, usize) {
        if let Some((t, b)) = self.margins {
            (t, std::cmp::min(b, self.lines.saturating_sub(1)))
        } else {
            (0, self.lines.saturating_sub(1))
        }
    }

    fn scroll_up(&mut self) {
        let (top, bottom) = self.get_margins();
        if top == 0 && bottom == self.lines.saturating_sub(1) {
            if let Some(dropped) = self.buffer.pop_front() {
                self.scrollback_buffer.push_back(dropped);
                if self.scrollback_buffer.len() > 5000 {
                    self.scrollback_buffer.pop_front();
                }
            }
            self.buffer.push_back(vec![self.blank_char(); self.columns]);
        } else {
            if top <= bottom && bottom < self.buffer.len() {
                self.buffer.remove(top);
                self.buffer.insert(bottom, vec![self.blank_char(); self.columns]);
            }
        }
    }

    fn scroll_down(&mut self) {
        let (top, bottom) = self.get_margins();
        if top <= bottom && bottom < self.buffer.len() {
            self.buffer.remove(bottom);
            self.buffer.insert(top, vec![self.blank_char(); self.columns]);
        }
    }

    fn newline(&mut self) {
        let (_, bottom) = self.get_margins();
        if self.cursor.y == bottom {
            self.scroll_up();
        } else if self.cursor.y < self.lines.saturating_sub(1) {
            self.cursor.y += 1;
        }
    }

    fn reverse_index(&mut self) {
        let (top, _) = self.get_margins();
        if self.cursor.y == top {
            self.scroll_down();
        } else if self.cursor.y > 0 {
            self.cursor.y -= 1;
        }
    }

    fn insert_lines(&mut self, count: usize) {
        let (top, bottom) = self.get_margins();
        if self.cursor.y < top || self.cursor.y > bottom { return; }
        for _ in 0..count {
            if bottom < self.buffer.len() {
                self.buffer.remove(bottom);
                self.buffer.insert(self.cursor.y, vec![self.blank_char(); self.columns]);
            }
        }
    }

    fn delete_lines(&mut self, count: usize) {
        let (top, bottom) = self.get_margins();
        if self.cursor.y < top || self.cursor.y > bottom { return; }
        for _ in 0..count {
            if self.cursor.y < self.buffer.len() {
                self.buffer.remove(self.cursor.y);
                self.buffer.insert(bottom, vec![self.blank_char(); self.columns]);
            }
        }
    }
}

impl Perform for Screen {
    fn print(&mut self, c: char) {
        if self.cursor.x >= self.columns {
            self.cursor.x = 0;
            self.newline();
        }
        let x = self.cursor.x;
        let y = self.cursor.y;
        if y < self.buffer.len() {
            let row = &mut self.buffer[y];
            if x < row.len() {
                let cell = &mut row[x];
                cell.data = c;
                cell.fg = self.current_style.fg;
                cell.bg = self.current_style.bg;
                cell.bold = self.current_style.bold;
                cell.italics = self.current_style.italics;
                cell.underscore = self.current_style.underscore;
                cell.inverse = self.current_style.inverse;
            }
        }
        self.cursor.x += 1;
    }

    fn execute(&mut self, byte: u8) {
        match byte {
            b'\n' | b'\x0B' | b'\x0C' => self.newline(),
            b'\r' => self.cursor.x = 0,
            b'\x08' => {
                if self.cursor.x > 0 {
                    self.cursor.x -= 1;
                }
            }
            _ => {}
        }
    }

    fn esc_dispatch(&mut self, intermediates: &[u8], _ignore: bool, byte: u8) {
        match (intermediates, byte) {
            (&[], b'M') => self.reverse_index(),
            (&[], b'D') => self.newline(),
            _ => {}
        }
    }

    fn csi_dispatch(&mut self, params: &Params, _intermediates: &[u8], _ignore: bool, action: char) {
        let get_param = |idx: usize, def: u16| -> u16 {
            let mut iter = params.iter();
            for _ in 0..idx {
                iter.next();
            }
            if let Some(p) = iter.next() {
                if p.len() > 0 { p[0] } else { def }
            } else {
                def
            }
        };

        match action {
            'A' => { // Cursor Up
                let n = get_param(0, 1) as usize;
                self.cursor.y = self.cursor.y.saturating_sub(n);
            }
            'B' => { // Cursor Down
                let n = get_param(0, 1) as usize;
                self.cursor.y = std::cmp::min(self.lines.saturating_sub(1), self.cursor.y + n);
            }
            'C' => { // Cursor Forward
                let n = get_param(0, 1) as usize;
                self.cursor.x = std::cmp::min(self.columns.saturating_sub(1), self.cursor.x + n);
            }
            'D' => { // Cursor Back
                let n = get_param(0, 1) as usize;
                self.cursor.x = self.cursor.x.saturating_sub(n);
            }
            'H' | 'f' => { // Cursor Position
                let r = std::cmp::max(1, get_param(0, 1)) as usize;
                let c = std::cmp::max(1, get_param(1, 1)) as usize;
                self.cursor.y = std::cmp::min(self.lines, r).saturating_sub(1);
                self.cursor.x = std::cmp::min(self.columns, c).saturating_sub(1);
            }
            'J' => { // Erase in Display
                let mode = get_param(0, 0);
                let cy = std::cmp::min(self.cursor.y, self.buffer.len().saturating_sub(1));
                let cx = if cy < self.buffer.len() {
                    std::cmp::min(self.cursor.x, self.buffer[cy].len().saturating_sub(1))
                } else { 0 };

                match mode {
                    0 => {
                        if cy < self.buffer.len() {
                            for x in cx..self.buffer[cy].len() {
                                self.buffer[cy][x] = self.blank_char();
                            }
                            for y in (cy + 1)..self.buffer.len() {
                                for x in 0..self.buffer[y].len() {
                                    self.buffer[y][x] = self.blank_char();
                                }
                            }
                        }
                    }
                    1 => {
                        for y in 0..cy {
                            if y < self.buffer.len() {
                                for x in 0..self.buffer[y].len() {
                                    self.buffer[y][x] = self.blank_char();
                                }
                            }
                        }
                        if cy < self.buffer.len() {
                            for x in 0..=cx {
                                if x < self.buffer[cy].len() {
                                    self.buffer[cy][x] = self.blank_char();
                                }
                            }
                        }
                    }
                    2 | 3 => {
                        for y in 0..self.buffer.len() {
                            for x in 0..self.buffer[y].len() {
                                self.buffer[y][x] = self.blank_char();
                            }
                        }
                    }
                    _ => {}
                }
            }
            'K' => { // Erase in Line
                let mode = get_param(0, 0);
                let cy = std::cmp::min(self.cursor.y, self.buffer.len().saturating_sub(1));
                let cx = if cy < self.buffer.len() {
                    std::cmp::min(self.cursor.x, self.buffer[cy].len().saturating_sub(1))
                } else { 0 };

                if cy < self.buffer.len() {
                    match mode {
                        0 => {
                            for x in cx..self.buffer[cy].len() {
                                self.buffer[cy][x] = self.blank_char();
                            }
                        }
                        1 => {
                            for x in 0..=cx {
                                if x < self.buffer[cy].len() {
                                    self.buffer[cy][x] = self.blank_char();
                                }
                            }
                        }
                        2 => {
                            for x in 0..self.buffer[cy].len() {
                                self.buffer[cy][x] = self.blank_char();
                            }
                        }
                        _ => {}
                    }
                }
            }
            'L' => { // IL - Insert Line
                let n = std::cmp::max(1, get_param(0, 1)) as usize;
                self.insert_lines(n);
            }
            'M' => { // DL - Delete Line
                let n = std::cmp::max(1, get_param(0, 1)) as usize;
                self.delete_lines(n);
            }
            'r' => { // DECSTBM - Set Top and Bottom Margins
                let top = std::cmp::max(1, get_param(0, 1)) as usize;
                let mut bot = get_param(1, 0) as usize;
                if bot == 0 { bot = self.lines; }
                bot = std::cmp::min(self.lines, bot);
                let top = std::cmp::min(self.lines, top);
                if top < bot {
                    self.margins = Some((top.saturating_sub(1), bot.saturating_sub(1)));
                } else {
                    self.margins = None;
                }
                self.cursor.x = 0;
                self.cursor.y = 0;
            }
            'm' => { // SGR - Colors
                if params.len() == 0 {
                    self.current_style = CurrentStyle::default();
                    return;
                }
                
                let mut flat_params = Vec::new();
                for param_list in params.iter() {
                    for &p in param_list {
                        flat_params.push(p);
                    }
                }
                
                let mut i = 0;
                while i < flat_params.len() {
                    let code = flat_params[i];
                    match code {
                        0 => self.current_style = CurrentStyle::default(),
                        1 => self.current_style.bold = true,
                        3 => self.current_style.italics = true,
                        4 => self.current_style.underscore = true,
                        7 => self.current_style.inverse = true,
                        22 => self.current_style.bold = false,
                        23 => self.current_style.italics = false,
                        24 => self.current_style.underscore = false,
                        27 => self.current_style.inverse = false,
                        30..=37 => {
                            let colors = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"];
                            self.current_style.fg = Color::Named(colors[(code - 30) as usize]);
                        }
                        38 => {
                            if i + 2 < flat_params.len() && flat_params[i+1] == 5 {
                                self.current_style.fg = Color::Indexed(flat_params[i+2] as u8);
                                i += 2;
                            } else if i + 4 < flat_params.len() && flat_params[i+1] == 2 {
                                self.current_style.fg = Color::Rgb(flat_params[i+2] as u8, flat_params[i+3] as u8, flat_params[i+4] as u8);
                                i += 4;
                            }
                        }
                        39 => self.current_style.fg = Color::Default,
                        40..=47 => {
                            let colors = ["black", "red", "green", "brown", "blue", "magenta", "cyan", "white"];
                            self.current_style.bg = Color::Named(colors[(code - 40) as usize]);
                        }
                        48 => {
                            if i + 2 < flat_params.len() && flat_params[i+1] == 5 {
                                self.current_style.bg = Color::Indexed(flat_params[i+2] as u8);
                                i += 2;
                            } else if i + 4 < flat_params.len() && flat_params[i+1] == 2 {
                                self.current_style.bg = Color::Rgb(flat_params[i+2] as u8, flat_params[i+3] as u8, flat_params[i+4] as u8);
                                i += 4;
                            }
                        }
                        49 => self.current_style.bg = Color::Default,
                        90..=97 => {
                            let colors = ["brightblack", "brightred", "brightgreen", "brightyellow", "brightblue", "brightmagenta", "brightcyan", "brightwhite"];
                            self.current_style.fg = Color::Named(colors[(code - 90) as usize]);
                        }
                        100..=107 => {
                            let colors = ["brightblack", "brightred", "brightgreen", "brightyellow", "brightblue", "brightmagenta", "brightcyan", "brightwhite"];
                            self.current_style.bg = Color::Named(colors[(code - 100) as usize]);
                        }
                        _ => {}
                    }
                    i += 1;
                }
             }
             _ => {}
        }
    }
}

#[pyclass]
pub struct Stream {
    parser: Parser,
}

#[pymethods]
impl Stream {
    #[new]
    pub fn new() -> Self {
        Stream {
            parser: Parser::new(),
        }
    }

    pub fn feed(&mut self, screen: &mut Screen, data: &str) {
        for byte in data.as_bytes() {
            self.parser.advance(screen, *byte);
        }
    }
}

#[pymodule]
fn rs_term(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_class::<Screen>()?;
    m.add_class::<Stream>()?;
    m.add_class::<PyChar>()?;
    m.add_class::<PyCursor>()?;
    Ok(())
}
