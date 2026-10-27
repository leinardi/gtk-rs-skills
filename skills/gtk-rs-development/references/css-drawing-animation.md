# CSS, custom drawing and animation

Read this when styling widgets, writing `style.css`, drawing a custom widget with `snapshot`, implementing a
paintable or layout, or animating anything.

Target: GTK 4.22 (`gtk` 0.11, `gnome_50` feature), libadwaita 1.9 (`adw` 0.9, `v1_9`). The GTK version is noted
where an API is newer than 4.10.

## Contents

- [Loading CSS](#loading-css)
- [Style classes](#style-classes)
- [Libadwaita CSS variables](#libadwaita-css-variables)
- [Common libadwaita style classes](#common-libadwaita-style-classes)
- [CSS names for custom widgets](#css-names-for-custom-widgets)
- [Drawing with snapshot](#drawing-with-snapshot)
- [Sizing: measure, size_allocate, layout managers](#sizing-measure-size_allocate-layout-managers)
- [Paintables](#paintables)
- [DrawingArea and cairo](#drawingarea-and-cairo)
- [Animations](#animations)
- [Pitfalls](#pitfalls)

## Loading CSS

```css
/* data/resources/style.css, auto-loaded from <resource-base-path>/style.css */
.status-dot {
  min-width: 8px;
  min-height: 8px;
  border-radius: 9999px;
  background-color: var(--success-bg-color);
}

.status-dot.offline {
  background-color: color-mix(in srgb, currentColor 30%, transparent);
}

@media (prefers-color-scheme: dark) {
  .hero-card { background-color: color-mix(in srgb, var(--accent-bg-color) 20%, var(--card-bg-color)); }
}

@media (prefers-contrast: more) {
  .hero-card { border: 1px solid var(--border-color); }
}
```

Why:

- `AdwApplication` loads `style.css` from the application's resource base path. The scaffold sets
  `resource-base-path` explicitly and bundles `style.css` at the root of the gresource prefix, so no Rust code is
  needed. The `.Devel` app id doesn't break the lookup because the base path has no `.Devel` suffix.
- One stylesheet with `@media (prefers-color-scheme: dark)` and `@media (prefers-contrast: more)` (GTK 4.16 /
  libadwaita 1.6) replaces `style-dark.css` and `style-hc.css`. Those files are still auto-loaded for
  compatibility, but splitting variants across files makes them drift.

### A manual CssProvider, only for dynamic CSS

```rust
// Once, e.g. in the window's constructed(): a provider whose content changes at runtime.
let provider = gtk::CssProvider::new();
gtk::style_context_add_provider_for_display(
    &WidgetExt::display(&*obj),
    &provider,
    gtk::STYLE_PROVIDER_PRIORITY_APPLICATION,
);
self.provider.set(provider).unwrap();

// Later, whenever the data changes:
let css = format!(":root {{ --album-color: {}; }}", rgba.to_str());
self.provider.get().unwrap().load_from_string(&css);
```

- Use this only when the CSS is computed at runtime (amberol recolors the window from album art:
  `src/window.rs`). For static styles, `style.css` is enough.
- `load_from_string` (4.12) replaces `load_from_data`. The free function
  `gtk::style_context_add_provider_for_display` replaces the deprecated `StyleContext::add_provider_for_display`.
  `gtk::StyleContext` as a whole is deprecated (4.10), so don't call `widget.style_context()`.
- Set CSS variables on `:root` or on a class, and keep the rules that use them in `style.css`. Regenerating
  whole rule sets per frame is slow.

## Style classes

```rust
row.add_css_class("error");
row.remove_css_class("error");
label.set_css_classes(&["title-2", "numeric"]);
let is_error = row.has_css_class("error");
```

```blp
Gtk.Label { label: _("Downloads"); styles ["heading"] }
Gtk.Button { label: _("_Install"); use-underline: true; styles ["pill", "suggested-action"] }
```

Classes describe what something is ("error", "heading"). CSS decides how that looks. Toggle classes for state
rather than setting colors from Rust. That keeps dark mode, high contrast and accent colors working.

## Libadwaita CSS variables

```css
my-meter > trough { background-color: color-mix(in srgb, currentColor 10%, transparent); }
my-meter > trough > block { background-color: var(--accent-bg-color); }
my-meter > trough > block.high { background-color: var(--warning-bg-color); }
.sidebar-footer { border-top: 1px solid var(--border-color); }
.subtle { opacity: var(--dim-opacity); }
```

- Colors come in triplets: `--accent-bg-color` / `--accent-fg-color` (a filled background and text on it)
  and `--accent-color` (standalone text or icons on a normal background). The same pattern applies to
  `--destructive-*`, `--success-*`, `--warning-*` and `--error-*`.
- Surfaces: `--window-bg-color`/`--window-fg-color`, `--view-bg-color`, `--card-bg-color`,
  `--headerbar-bg-color`, `--sidebar-bg-color`, `--popover-bg-color`, `--dialog-bg-color`. Others:
  `--border-color`, `--dim-opacity`, `--window-radius`.
- GTK 4.16+ CSS supports `var()`, `color-mix()`, relative colors (`oklab(from var(--accent-bg-color) l a b /
  50%)`) and `currentColor`. Derive tints from the theme with these instead of hard-coding hex values.
- `@define-color` and the named colors (`@accent_bg_color`, `@window_bg_color`, ...) are deprecated since
  libadwaita 1.6. They still resolve today, but new code should use the variables. Old apps in the corpus
  (helvum, Authenticator, mission-center) still use them, so don't copy from there.
- Overriding `--accent-bg-color` for one subtree is fine (a player tinted by artwork). Overriding it on `:root`
  replaces the user's chosen accent everywhere, so avoid that.

## Common libadwaita style classes

| Class | Use |
| --- | --- |
| `.dimmed` (1.7) | Secondary text or icons. Replaces `.dim-label`, which is deprecated |
| `.title-1` … `.title-4`, `.heading`, `.body`, `.caption`, `.caption-heading` | Type scale; use instead of font sizes |
| `.numeric` | Tabular figures for changing numbers (timers, sizes) |
| `.monospace` | Monospaced text |
| `.boxed-list` (on `Gtk.ListBox`), `.boxed-list-separate` (1.6) | Rounded card-style list of rows |
| `.card` | Card surface (also on a `Gtk.Button` or `Gtk.FlowBoxChild` for a clickable card) |
| `.suggested-action`, `.destructive-action` | One primary or one dangerous button per view |
| `.pill`, `.circular`, `.flat`, `.raised`, `.opaque` | Button shapes and weights |
| `.linked` | Joins adjacent buttons or entries in a box |
| `.toolbar` | Horizontal box of tool buttons with toolbar spacing |
| `.navigation-sidebar` (on ListBox/ListView) | Sidebar selection styling |
| `.osd` | Overlay controls over media |
| `.accent`, `.success`, `.warning`, `.error` | Colored text or icons |
| `.property` (on `ActionRow`, 1.4) | Swap title/subtitle emphasis for key-value rows |
| `.devel` (on the window) | Striped header bar for development builds; the scaffold adds it when `config::is_devel()` |

Look at the libadwaita "Style Classes" docs page for the full list. Run the app with the GTK inspector
(`Ctrl+Shift+D`, or `GTK_DEBUG=interactive`) to try classes and edit CSS live.

## CSS names for custom widgets

```rust
fn class_init(klass: &mut Self::Class) {
    klass.set_css_name("my-meter");
    klass.set_accessible_role(gtk::AccessibleRole::Meter);
}
```

```css
my-meter { min-height: 6px; }
my-meter:disabled { opacity: var(--dim-opacity); }
```

A CSS node name makes every instance styleable without adding a class to each one, and inspector shows it in the
node tree. Use a short, app-prefixed name so it can't clash with GTK nodes (video-trimmer: `vt-timeline` in
`src/timeline.rs`). Child parts are usually plain child widgets with a class, styled as `my-meter > .block`.

## Drawing with snapshot

```rust
use gtk::{graphene, gsk};

impl WidgetImpl for Sparkline {
    #[expect(clippy::cast_precision_loss)] // pixel sizes and sample counts are far below 2^23
    fn snapshot(&self, snapshot: &gtk::Snapshot) {
        let widget = self.obj();
        let (width, height) = (widget.width() as f32, widget.height() as f32);
        let bounds = graphene::Rect::new(0.0, 0.0, width, height);
        let color = widget.color(); // the CSS `color` of this widget (GTK 4.10)

        // Background, clipped to rounded corners.
        snapshot.push_rounded_clip(&gsk::RoundedRect::from_rect(bounds, 6.0));
        snapshot.append_color(&color.with_alpha(0.08), &bounds);
        snapshot.pop(); // every push_* needs exactly one pop

        // Line through the samples (GskPath, GTK 4.14).
        let values = self.values.borrow();
        if values.len() < 2 {
            return;
        }
        let builder = gsk::PathBuilder::new();
        let step = width / (values.len() - 1) as f32;
        for (i, value) in values.iter().enumerate() {
            let (x, y) = (i as f32 * step, height * (1.0 - value.clamp(0.0, 1.0)));
            if i == 0 { builder.move_to(x, y) } else { builder.line_to(x, y) }
        }
        let path = builder.to_path();
        snapshot.append_stroke(&path, &gsk::Stroke::new(2.0), &color);
    }
}

// In setters: change the data, then
self.obj().queue_draw();   // appearance changed
self.obj().queue_resize(); // size request changed (also redraws)
```

Why:

- `snapshot` records render nodes that GTK draws on the GPU. It's cheaper than cairo, scales correctly on
  fractional displays, and composes with CSS.
- `widget.color()` gives the foreground color from CSS, so the drawing follows dark mode, high contrast,
  `.dimmed`/`.accent` classes and `:disabled` with no extra code. For the accent color, add the `accent` class
  to the widget (then `color()` returns it), or read `adw::StyleManager::default().accent_color_rgba()` (1.6)
  and redraw on its notify signal.
- `gsk::PathBuilder` (`move_to`, `line_to`, `cubic_to`, `add_circle`, `add_rounded_rect`, `close`, `to_path`)
  plus `append_fill(&path, gsk::FillRule::Winding, &color)` / `append_stroke` (GTK 4.14) cover most shapes
  that used to need cairo.
- Other useful nodes: `append_texture`/`append_scaled_texture`, `append_linear_gradient`, `append_border`,
  `push_clip`, `push_opacity`, `save`/`translate`/`rotate`/`restore`. For a widget that also has child widgets,
  draw the children with `widget.snapshot_child(&child, snapshot)`.

Gotchas:

- Use `widget.width()`/`height()` inside `snapshot`. For coordinates relative to another widget, use
  `widget.compute_bounds(&other)` (returns `Option<graphene::Rect>`). `allocation()`,
  `allocated_width()`/`allocated_height()` are deprecated since 4.12 and fail `clippy -D warnings`.
- An unbalanced `push_*`/`pop` doesn't fail compilation. It produces broken rendering or criticals at runtime.
- Don't do heavy work in `snapshot`. It can run every frame. Precompute paths and textures when the data
  changes.

## Sizing: measure, size_allocate, layout managers

```rust
impl WidgetImpl for Sparkline {
    fn measure(&self, orientation: gtk::Orientation, _for_size: i32) -> (i32, i32, i32, i32) {
        match orientation {
            gtk::Orientation::Horizontal => (40, 120, -1, -1), // (minimum, natural, min_baseline, nat_baseline)
            _ => (16, 24, -1, -1),
        }
    }
}
```

- Leaf widgets: implement `measure` (and `snapshot`). Prefer CSS `min-width`/`min-height` when a fixed size is
  all you need. `measure` is for sizes that depend on data.
- Single child that should fill the widget: `klass.set_layout_manager_type::<gtk::BinLayout>()` in
  `class_init`, then `child.set_parent(&*obj)`. That's simpler than implementing `adw::Bin`-like logic by hand.
  Other built-ins: `gtk::BoxLayout`, `gtk::GridLayout`, `gtk::ConstraintLayout`, `gtk::CenterLayout`.
- Custom positioning: override `measure` and `size_allocate(&self, width, height, baseline)`, and place each
  child with `child.size_allocate(&gtk::Allocation::new(x, y, w, h), -1)` or `child.allocate(w, h, -1,
  Some(transform))`. Measure children with `child.measure(orientation, for_size)` first (video-trimmer:
  `src/timeline.rs`).
- Reusable layouts: subclass `gtk::LayoutManager` (`LayoutManagerImpl::measure(&self, widget, orientation,
  for_size)` and `allocate(&self, widget, width, height, baseline)`) and set it with `set_layout_manager_type`.
- Children you parent yourself must be unparented in `dispose` (`while let Some(child) = obj.first_child() {
  child.unparent(); }`). Template children of a `gtk::Widget`-derived template use `self.dispose_template()`
  instead. See `gobject.md`.

## Paintables

```rust
#[glib::object_subclass]
impl ObjectSubclass for QrPaintable {
    const NAME: &'static str = "AppQrPaintable";
    type Type = super::QrPaintable;
    type Interfaces = (gdk::Paintable,);
}

impl PaintableImpl for QrPaintable {
    fn intrinsic_width(&self) -> i32 { self.size.get() }
    fn intrinsic_height(&self) -> i32 { self.size.get() }

    #[expect(clippy::cast_possible_truncation)] // GTK passes pixel sizes, which fit in f32
    fn snapshot(&self, snapshot: &gdk::Snapshot, width: f64, height: f64) {
        // gdk::Snapshot converts to gtk::Snapshot, so the gtk SnapshotExt methods work here.
        let bounds = graphene::Rect::new(0.0, 0.0, width as f32, height as f32);
        snapshot.append_color(&gdk::RGBA::WHITE, &bounds);
        // ...
    }
}

// When the content changes:
self.obj().invalidate_contents(); // or invalidate_size() if the intrinsic size changed
```

A `gdk::Paintable` is drawable content without widget overhead. Show it with `Gtk.Picture { paintable: ...; }`,
`Gtk.Image`, `Adw.StatusPage`'s `paintable`, or as a texture source. Good for images, QR codes and animated
content (Authenticator: `src/widgets/accounts/qrcode_paintable.rs`; fractal:
`src/components/media/animated_image_paintable.rs`). Implement `flags()` returning `STATIC_SIZE`/`STATIC_CONTENTS`
when they apply, so GTK can cache it.

## DrawingArea and cairo

```rust
let area = gtk::DrawingArea::builder().content_width(200).content_height(100).build();
area.set_draw_func(|area, cr, width, height| {
    let color = area.color();
    cr.set_source_rgba(color.red().into(), color.green().into(), color.blue().into(), color.alpha().into());
    cr.arc(f64::from(width) / 2.0, f64::from(height) / 2.0, 20.0, 0.0, std::f64::consts::TAU);
    let _ = cr.fill();
});
// later: area.queue_draw();
```

`DrawingArea` is fine for a quick prototype or for porting existing cairo code. For a real, reusable widget,
subclass `gtk::Widget` and implement `snapshot`. You get CSS, a CSS name, an accessible role and GPU rendering.
Inside `snapshot`, `snapshot.append_cairo(&bounds)` gives a cairo context for the parts that still need it.

## Animations

```rust
// imp struct field: animation: OnceCell<adw::TimedAnimation>,
fn constructed(&self) {
    self.parent_constructed();
    let obj = self.obj();
    let target = adw::CallbackAnimationTarget::new(clone!(
        #[weak] obj,
        move |_value| obj.queue_draw()
    ));
    let animation = adw::TimedAnimation::builder()
        .widget(&*obj)
        .value_from(0.0)
        .value_to(1.0)
        .duration(250) // ms
        .easing(adw::Easing::EaseOutCubic)
        .target(&target)
        .build();
    self.animation.set(animation).unwrap();
}

// snapshot(): let progress = self.animation.get().unwrap().value();
// start: let anim = self.animation.get().unwrap(); anim.set_value_from(anim.value()); anim.set_value_to(1.0); anim.play();
```

```rust
// Animate a property directly.
let target = adw::PropertyAnimationTarget::new(&revealer_child, "opacity");
adw::TimedAnimation::new(&revealer_child, 0.0, 1.0, 200, target).play();

// Physics-based, interruptible motion (e.g. after a swipe): SpringParams::new(damping_ratio, mass, stiffness).
let spring = adw::SpringAnimation::new(&*obj, current, 0.0, adw::SpringParams::new(1.0, 1.0, 400.0), offset_target);
spring.set_initial_velocity(velocity);
spring.set_clamp(true);
spring.play();
```

Why:

- libadwaita animations are tied to the widget's frame clock. They pause while the widget is unmapped and skip to
  the end when the user has turned animations off (`gtk-enable-animations`, "Reduce Animation" in Settings).
  Keep `follow-enable-animations-setting` at its default `true` unless the motion conveys essential
  information.
- `TimedAnimation` is for fixed-duration transitions. `SpringAnimation` is for gesture-driven or interruptible
  motion, where it carries velocity over from the gesture.
- Keep one animation object per widget and retarget it (`set_value_from(anim.value())`, `set_value_to(..)`,
  `play()`). Creating a new one each time leaves the old one running. `connect_done` handles completion,
  `skip()` jumps to the end, and `reset()` rewinds.
- Built-in transitions (`Gtk.Revealer`, `Gtk.Stack` `transition-type`, `Adw.NavigationView`) already respect
  the setting. Use them before writing your own.

### Tick callbacks

```rust
obj.add_tick_callback(|widget, clock| {
    let t = clock.frame_time(); // microseconds, monotonic
    widget.queue_draw();
    glib::ControlFlow::Continue // Break removes the callback
});
```

Use `add_tick_callback` for continuous, open-ended motion (a live graph scrolling, a game loop). It does not
check `gtk-enable-animations` for you, so if the motion is decorative, check
`gtk::Settings::default().is_some_and(|s| s.is_gtk_enable_animations())` and show a static frame when it's off.
Keep the returned `TickCallbackId` and call `.remove()` when stopping. Always derive motion from
`frame_time()`, not from a per-frame increment, so speed doesn't depend on the refresh rate.

## Pitfalls

- A style in `style.css` has no effect → the file isn't at `<resource-base-path>/style.css`, or the gresource prefix
  differs from `resource-base-path` → check both in the gresource XML and `Application::new()`.
- Colors look wrong in dark mode or high contrast → hard-coded hex values or colors set from Rust → use
  `var(--…)`, `currentColor`, `color-mix()`, and `widget.color()` in `snapshot`.
- Deprecation warnings or clippy failures on `allocation()`, `style_context()`, `load_from_data`,
  `StyleContext::add_provider_for_display` → old API → `compute_bounds`/`width()`, CSS classes,
  `load_from_string`, `gtk::style_context_add_provider_for_display`.
- A custom widget never redraws after a property change → no `queue_draw()` (or `queue_resize()` for size)
  in the setter → call it after updating the state.
- Snapshot output is corrupted or GTK prints criticals about the snapshot stack → unbalanced `push_*`/`pop`,
  often an early `return` between them → pop before returning.
- An animation keeps running after the widget is gone, or a callback panics on upgrade → the
  `CallbackAnimationTarget` closure holds a strong reference → use `#[weak]` in `clone!`.
- `.dim-label` still used → deprecated in libadwaita 1.7 → `.dimmed`.
- `@define-color` / `@accent_bg_color` in new CSS → deprecated in libadwaita 1.6 → CSS variables.

## See also

- `libadwaita-ui.md`: widgets, StyleManager and the HIG.
- `gobject.md`: subclassing, properties, `dispose`.
- `closures-and-lifetimes.md`: `clone!` with `#[weak]`.
- `templates-and-blueprint.md`: `styles []` and templates.
- `accessibility-and-i18n.md`: roles and focus visuals for custom widgets.
- `deprecated-apis.md`: the full replacement table.
