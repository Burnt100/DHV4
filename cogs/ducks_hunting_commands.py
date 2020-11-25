import asyncio
import random
import time
from typing import Optional
from urllib.parse import quote_plus

import discord
from discord.ext import commands

from utils import checks
from utils.interaction import get_timedelta

from babel.dates import format_timedelta

from utils.cog_class import Cog
from utils.ctx_class import MyContext
from utils.levels import get_level_info
from utils.models import get_from_db, get_player, Player, get_random_player


def compute_luck(luck_pct):
    current = random.randint(1, 100)
    return current <= luck_pct


class DucksHuntingCommands(Cog):
    @commands.command(aliases=["pan", "pew", "pang", "shoot", "bong", "bonk", "kill", "itshighnoon", "its_high_noon", "killthatfuckingduck", "kill_that_fucking_duck", "kill_that_fucking_duck_omg"])
    @checks.channel_enabled()
    async def bang(self, ctx: MyContext, target: Optional[discord.Member], *args):
        """
        Shoot at the duck that appeared first on the channel.
        """
        _ = await ctx.get_translate_function()

        if not self.bot.is_ready():
            await ctx.reply(_("The bot is still starting up, please wait a minute and retry. Ducks haven't been lost."))
            return False

        db_hunter: Player = await get_player(ctx.author, ctx.channel, giveback=True)
        now = int(time.time())

        language_code = await ctx.get_language_code()

        if db_hunter.active_powerups["dead"] > 0:
            db_hunter.shooting_stats['shots_when_dead'] += 1
            await db_hunter.save()
            await ctx.reply(_("☠️ It's a little cold in there... Maybe because **you are DEAD**! have you tried eating BRAINS ? `{ctx.prefix}revive`...",
                              ctx=ctx,
                              ))
            return False


        if db_hunter.active_powerups['wet'] > now:
            db_hunter.shooting_stats['shots_when_wet'] += 1
            await db_hunter.save()

            td = get_timedelta(db_hunter.active_powerups['wet'], now)
            await ctx.reply(_("🚰 Dude... Your clothes are wet, at least dry them (for **{time_delta}**) or something, or buy new ones (`{ctx.prefix}shop clothes`)",
                              ctx=ctx,
                              time_delta=format_timedelta(td, locale=language_code)))
            return False

        if db_hunter.weapon_confiscated:
            db_hunter.shooting_stats['shots_when_confiscated'] += 1
            await db_hunter.save()

            await ctx.reply(_("⛔️ Dude... Your weapon has been confiscated. Wait for freetime (`{ctx.prefix}freetime`), or buy it back in the shop (`{ctx.prefix}shop weapon`)",
                              ctx=ctx))
            return False

        sabotage = db_hunter.weapon_sabotaged_by

        if sabotage:
            sabotager = await db_hunter.weapon_sabotaged_by.get().prefetch_related("member__user").get()

            db_hunter.weapon_sabotaged_by = None
            db_hunter.weapon_jammed = True
            db_hunter.shooting_stats['shots_when_sabotaged'] += 1
            await db_hunter.save()

            await ctx.reply(_("💥 Your weapon was sabotaged and exploded in your face. You can thank "
                              "{sabotager.name}#{sabotager.discriminator} for this bad joke.",
                              sabotager=sabotager.member.user))
            return False

        if db_hunter.weapon_jammed:
            db_hunter.shooting_stats['shots_when_jammed'] += 1
            await db_hunter.save()

            await ctx.reply(_("☁️ Your weapon is jammed. Reload it to clean it up !"))
            return False

        if db_hunter.bullets <= 0:
            db_hunter.shooting_stats['shots_with_empty_magazine'] += 1
            await db_hunter.save()

            level_info = db_hunter.level_info()

            await ctx.reply(_("🦉 Magazine empty ! Reload or buy bullets | **Bullets**: 0/{max_bullets} | Magazines: {current_magazines}/{max_magazines}",
                              max_bullets=level_info['bullets'],
                              max_magazines=level_info['magazines'],
                              current_magazines=db_hunter.magazines))
            return False

        # Jamming
        level_info = get_level_info(db_hunter.experience)
        lucky = compute_luck(level_info['reliability'])
        if db_hunter.is_powerup_active('grease'):
            lucky = lucky or compute_luck(level_info['reliability'])
        elif db_hunter.active_powerups['sand'] > 0:
            db_hunter.active_powerups['sand'] -= 1
            lucky = lucky and compute_luck(level_info['reliability'])

        if not lucky:
            db_hunter.shooting_stats['shots_jamming_weapon'] += 1
            db_hunter.weapon_jammed = True
            await db_hunter.save()
            await ctx.reply(_("💥 Your weapon jammed. Consider buying grease next time."))
            return False

        db_hunter.bullets -= 1
        db_hunter.shooting_stats['bullets_used'] += 1
        db_channel = await get_from_db(ctx.channel)

        # Missing
        accuracy = level_info['accuracy']
        if db_hunter.active_powerups['mirror'] > 0:
            accuracy /= 2
            db_hunter.active_powerups['mirror'] -= 1

        if db_hunter.active_powerups['sight'] > 0:
            accuracy += int((100 - accuracy) / 3)
            db_hunter.active_powerups['sight'] -= 1

        missed = not compute_luck(accuracy)
        homing = db_hunter.active_powerups["homing_bullets"] >= 1

        if homing:
            db_hunter.active_powerups["homing_bullets"] -= 1
            missed = False
            target = ctx.author

        if (missed and not target) or (target and not missed):
            if missed:
                db_hunter.shooting_stats['missed'] += 1
                db_hunter.experience -= 2
            elif homing:
                db_hunter.shooting_stats['homing_kills'] += 1
                db_hunter.shooting_stats['missed'] += 1
                db_hunter.experience -= 2

            # Killing
            killed_someone = target or compute_luck(db_channel.kill_on_miss_chance)

            if killed_someone:
                db_hunter.shooting_stats['killed'] += 1
                db_hunter.experience -= 15
                db_hunter.weapon_confiscated = True

                if not target:
                    db_target: Player = await get_random_player(db_channel)
                else:
                    db_target: Player = await get_player(target, ctx.channel)

                    if db_target.id == db_hunter.id:
                        db_target = db_hunter

                    db_hunter.shooting_stats['murders'] += 1

                db_target.shooting_stats['got_killed'] += 1
                db_target.active_powerups["dead"] += 1

                if db_target.id != db_hunter.id:
                    await asyncio.gather(db_target.save(), db_hunter.save())  # Save both at the same time
                else:
                    await db_hunter.save()

                if db_channel.mentions_when_killed:
                    player_name = f"<@{db_target.member.user.discord_id}>"
                else:
                    player_name = db_target.member.user.name

                if homing:
                    await ctx.reply(_("✨ You take the new homing bullets outside of their packaging, place them in your weapon and shoot with your eyes closed...",
                                      ))
                    await asyncio.sleep(2)
                    await ctx.reply(_("... And the bullet flew straight into your face, killing you instantly. "
                                      "You should send your complaints to the CACAC. [**WEAPON CONFISCATED**][**MISSED**: -2 exp][**MURDER**: -15 exp]",
                                      ))
                elif target:
                    if target.id == ctx.author.id:
                        await ctx.reply(_("🔫 You commited suicide. [**WEAPON CONFISCATED**][**MURDER**: -15 exp]",
                                          ))
                    else:
                        await ctx.reply(_("🔫 You took your weapon out, aiming it directly towards {player_name} head, and pulled the trigger. "
                                          "[**WEAPON CONFISCATED**][**MURDER**: -15 exp]",
                                          player_name=player_name,
                                          ))
                else:
                    await ctx.reply(_("🔫 You missed the duck... And shot {player_name} in the head, killing him on the spot. "
                                      "[**WEAPON CONFISCATED**][**MISSED**: -2 exp][**MURDER**: -15 exp]",
                                      player_name=player_name,
                                      ))

                await ctx.send(f"http://www.tombstonebuilder.com/generate.php?top1={quote_plus(db_target.member.user.name)}&top2={quote_plus(_('Forgot to duck'))}&top3=&top4=&sp=")
            else:
                await db_hunter.save()
                await ctx.reply(_("🌲 What did you try to aim at ? I guess you shot that tree, over here. [**MISSED**: -2 exp]",))

            return False

        await db_hunter.save()
        duck = await ctx.target_next_duck()

        if duck:
            db_hunter.shooting_stats['shots_with_duck'] += 1
            await duck.shoot(args)
        elif db_hunter.active_powerups['detector'] >= 1:
            db_hunter.active_powerups['detector'] -= 1
            db_hunter.shooting_stats['shots_stopped_by_detector'] += 1
            db_hunter.shooting_stats['bullets_used'] -= 1
            db_hunter.bullets += 1
            await db_hunter.save()
            await ctx.reply(_("🕵️ Woah there ! Calm down, there are no ducks. Your infrared detector stopped the shot.", ))
        else:
            db_hunter.shooting_stats['shots_without_ducks'] += 1
            db_hunter.experience -= 2
            await db_hunter.save()
            await ctx.reply(_("❓️ What are you trying to kill exactly ? There are no ducks here. [**MISSED**: -2 exp]"))

    @commands.command(aliases=["rl"])
    @checks.channel_enabled()
    async def reload(self, ctx: MyContext):
        """
        Reload your gun.
        """
        _ = await ctx.get_translate_function()
        db_hunter: Player = await get_player(ctx.author, ctx.channel, giveback=True)
        now = int(time.time())

        if db_hunter.weapon_confiscated:
            db_hunter.shooting_stats['reloads_when_confiscated'] += 1
            await db_hunter.save()

            await ctx.reply(_("Dude... You don't have a weapon, it has been confiscated. "
                              "Wait for freetime (`{ctx.prefix}freetime`), or buy it back in the shop (`{ctx.prefix}shop weapon`)",
                              ctx=ctx))
            return False

        if db_hunter.weapon_jammed:
            db_hunter.weapon_jammed = False
            await db_hunter.save()

            await ctx.reply(_("☀️️ You unjam your weapon !"))
            return True

        level_info = get_level_info(db_hunter.experience)

        if db_hunter.bullets <= 0 and db_hunter.magazines >= 1:
            db_hunter.shooting_stats['reloads'] += 1
            db_hunter.magazines -= 1
            db_hunter.bullets = level_info["bullets"]

            await db_hunter.save()

            await ctx.reply(_("🦉 You reloaded your weapon | Bullets: {current_bullets}/{max_bullets} | Magazines: {current_magazines}/{max_magazines} ",
                              current_bullets=db_hunter.bullets,
                              max_bullets=level_info["bullets"],
                              current_magazines=db_hunter.magazines,
                              max_magazines=level_info["magazines"]))
            return True
        elif db_hunter.bullets > 0:
            db_hunter.shooting_stats['unneeded_reloads'] += 1
            await db_hunter.save()

            await ctx.reply(_("🦉 You don't need to reload your weapon | **Bullets**: {current_bullets}/{max_bullets} | Magazines: {current_magazines}/{max_magazines} ",
                              current_bullets=db_hunter.bullets,
                              max_bullets=level_info["bullets"],
                              current_magazines=db_hunter.magazines,
                              max_magazines=level_info["magazines"]))
            return False
        elif db_hunter.magazines <= 0:
            db_hunter.shooting_stats['unneeded_reloads'] += 1
            await db_hunter.save()
            await ctx.reply(_("🦉 You don't have any magazines. `{ctx.prefix}shop magazine` | "
                              "Bullets: {current_bullets}/{max_bullets} | **Magazines**: {current_magazines}/{max_magazines} ",
                              current_bullets=db_hunter.bullets,
                              max_bullets=level_info["bullets"],
                              current_magazines=db_hunter.magazines,
                              max_magazines=level_info["magazines"]))
            return False

    @commands.command()
    @checks.channel_enabled()
    async def hug(self, ctx: MyContext, target: Optional[discord.Member], *args):
        """
        Hug the duck that appeared first on the channel.
        """
        _ = await ctx.get_translate_function()

        if not self.bot.is_ready():
            await ctx.reply(_("The bot is still starting up, please wait a minute and retry. Ducks haven't been lost."))
            return False

        db_hunter: Player = await get_player(ctx.author, ctx.channel)

        if db_hunter.active_powerups["dead"] > 0:
            db_hunter.hugged['when_dead'] += 1
            await db_hunter.save()
            await ctx.reply(_("☠️ You are a little too dead to hug, go `{ctx.prefix}revive` yourself",
                              ctx=ctx,
                              ))
            return False

        if target:
            db_hunter.hugged['players'] += 1
            await db_hunter.save()
            await ctx.reply(_("{you.mention} hugged {other.mention}. They feel loved.", you=ctx.author, other=target))
            return

        duck = await ctx.target_next_duck()
        if duck:
            await duck.hug(args)
        else:
            await ctx.reply(_("What are you trying to hug, exactly? A tree?"))
            db_hunter.hugged["nothing"] += 1
            await db_hunter.save()

    @commands.command(aliases=["cpr", "brains", "zombie", "undead"])
    @checks.channel_enabled()
    async def revive(self, ctx: MyContext):
        """
        Revive yourself by eating brains
        """
        _ = await ctx.get_translate_function()

        db_hunter: Player = await get_player(ctx.author, ctx.channel)

        dead_times = db_hunter.active_powerups["dead"]

        if dead_times == 0:
            db_hunter.shooting_stats['useless_revives'] += 1
            await db_hunter.save()
            await ctx.reply(_("You are already alive and well."))
            return

        else:
            db_hunter.active_powerups["dead"] = 0
            db_hunter.shooting_stats['revives'] += 1
            db_hunter.shooting_stats['brains_eaten'] += dead_times
            db_hunter.shooting_stats['max_brains_eaten_at_once'] = max(db_hunter.shooting_stats['max_brains_eaten_at_once'], dead_times)
            await db_hunter.save()

            await ctx.reply(_("You eat {brains} 🧠 and regain consiousness.", brains=dead_times))



setup = DucksHuntingCommands.setup
