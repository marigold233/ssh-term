use pyo3::prelude::*;
use std::collections::VecDeque;
use vte::{Params, Parser, Perform};
use unicode_width::UnicodeWidthChar;

#[derive(Clone, Copy, PartialEq, Eq)]
pub enum Color {
    Default,
    Indexed(u8),
    Rgb(u8, u8, u8),
}

impl Color {
    fn to_tuple(&self) -> (u8, u8, u8, u8) {
        match self {
            Color::Default => (0, 0, 0, 0),
            Color::Indexed(idx) => (1, *idx, 0, 0),
            Color::Rgb(r, g, b) => (2, *r, *g, *b),
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
    row_pool: Vec<Vec<PyChar>>,
    alt_buffer: Option<VecDeque<Vec<PyChar>>>,
    alt_cursor: Option<PyCursor>,
    dirty_lines: Vec<bool>,
    #[pyo3(get)]
    pub bracketed_paste: bool,
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
            row_pool: Vec::with_capacity(500),
            alt_buffer: None,
            alt_cursor: None,
            dirty_lines: vec![true; lines],
            bracketed_paste: false,
        }
    }

    pub fn resize(&mut self, lines: usize, columns: usize) {
        let lines = std::cmp::max(1, lines);
        let columns = std::cmp::max(1, columns);
        self.lines = lines;
        self.columns = columns;
        let old_lines = self.buffer.len();
        if lines > old_lines {
            for _ in old_lines..lines {
                let row = self.blank_row();
                self.buffer.push_back(row);
            }
        } else if lines < old_lines {
            for _ in lines..old_lines {
                if let Some(r) = self.buffer.pop_back() {
                    if self.row_pool.len() < 500 { self.row_pool.push(r); }
                }
            }
        }
        let blank = self.blank_char();
        for row in self.buffer.iter_mut() {
            row.resize(columns, blank);
        }
        if self.cursor.y >= lines {
            self.cursor.y = lines.saturating_sub(1);
        }
        if self.cursor.x >= columns {
            self.cursor.x = columns.saturating_sub(1);
        }
        self.margins = None;
        self.dirty_lines.resize(lines, true);
        for i in 0..lines { self.dirty_lines[i] = true; }
    }

    
    #[pyo3(signature=())]
    pub fn get_and_clear_dirty_lines(&mut self) -> Vec<usize> {
        let mut dirty = Vec::new();
        for (i, d) in self.dirty_lines.iter_mut().enumerate() {
            if *d {
                dirty.push(i);
                *d = false;
            }
        }
        dirty
    }

    fn blank_row(&mut self) -> Vec<PyChar> {
        if let Some(mut row) = self.row_pool.pop() {
            row.resize(self.columns, self.blank_char());
            for cell in row.iter_mut() {
                *cell = self.blank_char();
            }
            row
        } else {
            vec![self.blank_char(); self.columns]
        }
    }

    fn mark_dirty(&mut self, y: usize) {
        if y < self.lines {
            self.dirty_lines[y] = true;
        }
    }

    pub fn get_total_lines(&self) -> usize {
        self.scrollback_buffer.len() + self.lines
    }

    pub fn get_line_segments(&self, y: usize) -> Vec<(String, (u8, u8, u8, u8), (u8, u8, u8, u8), bool, bool, bool, bool, bool)> {
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
            if cell.data == '\0' { continue; } // skip wide-char dummy cells
            let is_cursor = !in_history && (y - self.scrollback_buffer.len()) == self.cursor.y && x == self.cursor.x;
            
            let style = (cell.fg, cell.bg, cell.bold, cell.italics, cell.underscore, cell.inverse, is_cursor);

            if let Some(cs) = current_style {
                if cs == style {
                    current_text.push(cell.data);
                } else {
                    segments.push((
                        current_text.clone(),
                        cs.0.to_tuple(),
                        cs.1.to_tuple(),
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
                    cs.0.to_tuple(),
                    cs.1.to_tuple(),
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

    #[pyo3(signature=(y, cursor_visible, scroll_offset))]
    pub fn get_line_ansi(&self, y: usize, cursor_visible: bool, scroll_offset: usize) -> String {
        let segments = self.get_line_segments(y);
        let mut ansi = String::new();
        
        if segments.is_empty() {
            return ansi;
        }

        let mut last_bold = false;
        let mut last_italics = false;
        let mut last_underscore = false;
        let mut last_inverse = false;
        let mut last_fg = (0, 0, 0, 0);
        let mut last_bg = (0, 0, 0, 0);

        ansi.push_str("\x1b[0m");

        for (text, fg, bg, bold, italics, underscore, inverse, is_cursor) in segments {
            let is_reversed = (is_cursor && cursor_visible && scroll_offset == 0) || inverse;
            
            let mut sgr = Vec::new();

            if bold && !last_bold { sgr.push(String::from("1")); }
            if !bold && last_bold { sgr.push(String::from("22")); }
            
            if italics && !last_italics { sgr.push(String::from("3")); }
            if !italics && last_italics { sgr.push(String::from("23")); }

            if underscore && !last_underscore { sgr.push(String::from("4")); }
            if !underscore && last_underscore { sgr.push(String::from("24")); }

            if is_reversed && !last_inverse { sgr.push(String::from("7")); }
            if !is_reversed && last_inverse { sgr.push(String::from("27")); }

            if fg != last_fg {
                match fg.0 {
                    1 => {
                        let c = fg.1;
                        if c < 8 { sgr.push(format!("{}", 30 + c)); }
                        else if c < 16 { sgr.push(format!("{}", 90 + c - 8)); }
                        else { sgr.push(format!("38;5;{}", c)); }
                    },
                    2 => sgr.push(format!("38;2;{};{};{}", fg.1, fg.2, fg.3)),
                    _ => sgr.push(String::from("39")),
                }
            }
            if bg != last_bg {
                match bg.0 {
                    1 => {
                        let c = bg.1;
                        if c < 8 { sgr.push(format!("{}", 40 + c)); }
                        else if c < 16 { sgr.push(format!("{}", 100 + c - 8)); }
                        else { sgr.push(format!("48;5;{}", c)); }
                    },
                    2 => sgr.push(format!("48;2;{};{};{}", bg.1, bg.2, bg.3)),
                    _ => sgr.push(String::from("49")),
                }
            }

            if !sgr.is_empty() {
                ansi.push_str("\x1b[");
                ansi.push_str(&sgr.join(";"));
                ansi.push('m');
            }

            ansi.push_str(&text);

            last_bold = bold;
            last_italics = italics;
            last_underscore = underscore;
            last_inverse = is_reversed;
            last_fg = fg;
            last_bg = bg;
        }

        ansi.push_str("\x1b[0m");
        ansi
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
                    if let Some(recycled) = self.scrollback_buffer.pop_front() {
                        if self.row_pool.len() < 500 { self.row_pool.push(recycled); }
                    }
                }
            }
            let new_row = self.blank_row();
            self.buffer.push_back(new_row);
        } else {
            if top <= bottom && bottom < self.buffer.len() {
                if let Some(dropped) = self.buffer.remove(top) {
                    if self.row_pool.len() < 500 { self.row_pool.push(dropped); }
                }
                let new_row = self.blank_row();
                self.buffer.insert(bottom, new_row);
            }
        }
        for i in top..=bottom {
            self.mark_dirty(i);
        }
    }

    fn scroll_down(&mut self) {
        let (top, bottom) = self.get_margins();
        if top <= bottom && bottom < self.buffer.len() {
            if let Some(dropped) = self.buffer.remove(bottom) {
                if self.row_pool.len() < 500 { self.row_pool.push(dropped); }
            }
            let new_row = self.blank_row();
            self.buffer.insert(top, new_row);
            for i in top..=bottom {
                self.mark_dirty(i);
            }
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
                if let Some(dropped) = self.buffer.remove(bottom) {
                    if self.row_pool.len() < 500 { self.row_pool.push(dropped); }
                }
                let new_row = self.blank_row();
                self.buffer.insert(self.cursor.y, new_row);
            }
        }
        for i in self.cursor.y..=bottom {
            self.mark_dirty(i);
        }
    }

    fn delete_lines(&mut self, count: usize) {
        let (top, bottom) = self.get_margins();
        if self.cursor.y < top || self.cursor.y > bottom { return; }
        for _ in 0..count {
            if self.cursor.y < self.buffer.len() {
                if let Some(dropped) = self.buffer.remove(self.cursor.y) {
                    if self.row_pool.len() < 500 { self.row_pool.push(dropped); }
                }
                let new_row = self.blank_row();
                self.buffer.insert(bottom, new_row);
            }
        }
        for i in self.cursor.y..=bottom {
            self.mark_dirty(i);
        }
    }
}

impl Perform for Screen {
    fn print(&mut self, c: char) {
        let w = c.width().unwrap_or(1);
        if w == 0 { return; } // ignore zero-width characters for now

        if self.cursor.x + w > self.columns {
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

                if w > 1 {
                    for i in 1..w {
                        if x + i < row.len() {
                            let dummy = &mut row[x+i];
                            dummy.data = '\0';
                            dummy.fg = self.current_style.fg;
                            dummy.bg = self.current_style.bg;
                        }
                    }
                }
            }
        }
        self.cursor.x += w;
        let cy = self.cursor.y;
        self.mark_dirty(cy);
    }

    fn execute(&mut self, byte: u8) {
        match byte {
            b'\n' | b'\x0B' | b'\x0C' => {
                let cy = self.cursor.y;
                self.newline();
                self.mark_dirty(cy);
                let ny = self.cursor.y;
                self.mark_dirty(ny);
            }
            b'\r' => {
                self.cursor.x = 0;
                let cy = self.cursor.y;
                self.mark_dirty(cy);
            }
            b'\x08' => {
                if self.cursor.x > 0 {
                    self.cursor.x -= 1;
                    let cy = self.cursor.y;
                    self.mark_dirty(cy);
                }
            }
            _ => {}
        }
    }

    fn esc_dispatch(&mut self, intermediates: &[u8], _ignore: bool, byte: u8) {
        match (intermediates, byte) {
            (&[], b'M') => {
                let cy = self.cursor.y;
                self.reverse_index();
                self.mark_dirty(cy);
                let ny = self.cursor.y;
                self.mark_dirty(ny);
            }
            (&[], b'D') => {
                let cy = self.cursor.y;
                self.newline();
                self.mark_dirty(cy);
                let ny = self.cursor.y;
                self.mark_dirty(ny);
            }
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
            'h' | 'l' => {
                let is_set = action == 'h';
                if _intermediates.starts_with(b"?") {
                    let param = get_param(0, 0);
                    if param == 1049 {
                        if is_set {
                            if self.alt_buffer.is_none() {
                                let mut alt = VecDeque::with_capacity(self.lines);
                                for _ in 0..self.lines { alt.push_back(self.blank_row()); }
                                self.alt_buffer = Some(std::mem::take(&mut self.buffer));
                                self.buffer = alt;
                                self.alt_cursor = Some(self.cursor.clone());
                                self.cursor.x = 0;
                                self.cursor.y = 0;
                                for i in 0..self.lines { self.mark_dirty(i); }
                            }
                        } else {
                            if let Some(alt) = self.alt_buffer.take() {
                                let discarded = std::mem::take(&mut self.buffer);
                                for r in discarded { if self.row_pool.len() < 500 { self.row_pool.push(r); } }
                                self.buffer = alt;
                                if let Some(c) = self.alt_cursor.take() { self.cursor = c; }
                                for i in 0..self.lines { self.mark_dirty(i); }
                            }
                        }
                    } else if param == 2004 {
                        self.bracketed_paste = is_set;
                    }
                }
            }
            'A' => { // Cursor Up
                let old_y = self.cursor.y;
                let n = get_param(0, 1) as usize;
                self.cursor.y = self.cursor.y.saturating_sub(n);
                self.mark_dirty(old_y);
                let ny = self.cursor.y;
                self.mark_dirty(ny);
            }
            'B' => { // Cursor Down
                let old_y = self.cursor.y;
                let n = get_param(0, 1) as usize;
                self.cursor.y = std::cmp::min(self.lines.saturating_sub(1), self.cursor.y + n);
                self.mark_dirty(old_y);
                let ny = self.cursor.y;
                self.mark_dirty(ny);
            }
            'C' => { // Cursor Forward
                let n = get_param(0, 1) as usize;
                self.cursor.x = std::cmp::min(self.columns.saturating_sub(1), self.cursor.x + n);
                let cy = self.cursor.y;
                self.mark_dirty(cy);
            }
            'D' => { // Cursor Back
                let n = get_param(0, 1) as usize;
                self.cursor.x = self.cursor.x.saturating_sub(n);
                let cy = self.cursor.y;
                self.mark_dirty(cy);
            }
            'H' | 'f' => { // Cursor Position
                let old_y = self.cursor.y;
                let r = std::cmp::max(1, get_param(0, 1)) as usize;
                let c = std::cmp::max(1, get_param(1, 1)) as usize;
                self.cursor.y = std::cmp::min(self.lines, r).saturating_sub(1);
                self.cursor.x = std::cmp::min(self.columns, c).saturating_sub(1);
                self.mark_dirty(old_y);
                let ny = self.cursor.y;
                self.mark_dirty(ny);
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
                            self.mark_dirty(cy);
                            for y in (cy + 1)..self.buffer.len() {
                                for x in 0..self.buffer[y].len() {
                                    self.buffer[y][x] = self.blank_char();
                                }
                                self.mark_dirty(y);
                            }
                        }
                    }
                    1 => {
                        for y in 0..cy {
                            if y < self.buffer.len() {
                                for x in 0..self.buffer[y].len() {
                                    self.buffer[y][x] = self.blank_char();
                                }
                                self.mark_dirty(y);
                            }
                        }
                        if cy < self.buffer.len() {
                            for x in 0..=cx {
                                if x < self.buffer[cy].len() {
                                    self.buffer[cy][x] = self.blank_char();
                                }
                            }
                            self.mark_dirty(cy);
                        }
                    }
                    2 | 3 => {
                        for y in 0..self.buffer.len() {
                            for x in 0..self.buffer[y].len() {
                                self.buffer[y][x] = self.blank_char();
                            }
                            self.mark_dirty(y);
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
                    self.mark_dirty(cy);
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
                let old_y = self.cursor.y;
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
                self.mark_dirty(old_y);
                self.mark_dirty(0);
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
                            self.current_style.fg = Color::Indexed((code - 30) as u8);
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
                            self.current_style.bg = Color::Indexed((code - 40) as u8);
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
                            self.current_style.fg = Color::Indexed((code - 90 + 8) as u8);
                        }
                        100..=107 => {
                            self.current_style.bg = Color::Indexed((code - 100 + 8) as u8);
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
