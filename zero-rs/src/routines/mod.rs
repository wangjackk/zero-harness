//! 业务 routine 集合:注册进 registry 供启动程序共用。

pub mod audio;
pub mod music;
pub mod speak;

use routine::core::RoutineRegistry;

/// 聚合本 crate 全部 routine(启动程序共用)。
pub fn registry() -> RoutineRegistry {
    let mut routines = RoutineRegistry::new();
    routines.add::<speak::SpeakRoutine>();
    routines.add::<music::play_music::PlayMusic>();
    routines.add::<music::sound_effect::SoundEffect>();
    routines.add::<music::list_music::ListMusic>();
    routines.add::<audio::set::SetOutput>();
    routines.add::<audio::list::ListOutputs>();
    routines
}
