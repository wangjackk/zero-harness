//! 启动 banner:业务侧自行渲染已注册 routine 的展示表格.
//!
//! 框架(routine)只打印一行客观统计(``print_summary``),不碰
//! description/modules/hidden 等业务字段——那些是业务侧的展示关心,放这里.
//! 对齐 Python `app/routines/banner.py` 的审美:统计行 + Modules + Routines
//! 表格,hidden 行加 tag 并压暗.

use std::io::IsTerminal;

use routine::core::RoutineRegistry;

const DIM: &str = "\x1b[2m";
const BOLD: &str = "\x1b[1m";
const CYAN: &str = "\x1b[36m";
const RESET: &str = "\x1b[0m";

/// 打印 routine 展示表格.在 hub.connect 之前调用.
pub fn print_banner(registry: &RoutineRegistry, modules: &[String]) {
    let use_color = std::io::stdout().is_terminal();
    let c = |s: &str, code: &str| {
        if use_color && !code.is_empty() {
            format!("{code}{s}{RESET}")
        } else {
            s.to_string()
        }
    };

    let rs = registry.get_routines();
    let enabled = rs.iter().filter(|f| f.enabled()).count();
    let hidden_n = rs
        .iter()
        .filter(|f| {
            f.meta()
                .get("hidden")
                .and_then(|v| v.as_bool())
                .unwrap_or(false)
        })
        .count();
    let passive_n = rs.iter().filter(|f| f.is_passive()).count();

    println!();
    println!(
        "  {}  {}",
        c("zero-rs", &format!("{BOLD}{DIM}")),
        c(
            &format!(
                "{} routines  ·  {} enabled  ·  {} hidden  ·  {} passive",
                rs.len(),
                enabled,
                hidden_n,
                passive_n
            ),
            DIM
        )
    );
    println!();

    // Modules
    println!("  {} {}", c("──", DIM), c("Modules", BOLD));
    let mod_names = modules.join(" · ");
    println!("  {}", c(&mod_names, DIM));
    println!();

    // Routines 表格:名字列按最长名对齐(业务 routine 名为 ASCII),描述列不做截断.
    let name_w = rs
        .iter()
        .map(|f| f.routine_name().len())
        .max()
        .unwrap_or(4)
        .max(4)
        + 2;
    let type_w = 8;

    println!("  {} {}", c("──", DIM), c("Routines", BOLD));
    println!(
        "  {}  {}  {}",
        c(&pad("NAME", name_w), DIM),
        c(&pad("TYPE", type_w), DIM),
        c("DESCRIPTION", DIM)
    );
    println!("  {}", c(&"─".repeat(60), DIM));

    let mut rows: Vec<_> = rs
        .iter()
        .map(|f| {
            let hidden = f
                .meta()
                .get("hidden")
                .and_then(|v| v.as_bool())
                .unwrap_or(false);
            let type_raw = if f.is_passive() {
                "passive"
            } else if f.enabled() {
                "active"
            } else {
                "disabled"
            };
            let desc = f
                .meta()
                .get("description")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string();
            (
                f.routine_name().to_string(),
                type_raw.to_string(),
                desc,
                hidden,
            )
        })
        .collect();
    // (is_passive, name) 排序,对齐 Python banner.
    rows.sort_by(|a, b| a.0.cmp(&b.0));

    for (name, type_raw, desc, hidden) in rows {
        let tag = if hidden { "[hidden] " } else { "" };
        let line_code = if hidden { DIM } else { "" };
        let type_code = if type_raw == "passive" { CYAN } else { "" };
        println!(
            "  {}  {}  {}",
            c(&pad(&name, name_w), line_code),
            c(
                &pad(&type_raw, type_w),
                if type_code.is_empty() {
                    line_code
                } else {
                    type_code
                }
            ),
            c(&format!("{tag}{desc}"), line_code)
        );
    }
    println!();
}

fn pad(s: &str, w: usize) -> String {
    let mut out = s.to_string();
    while out.len() < w {
        out.push(' ');
    }
    out
}
