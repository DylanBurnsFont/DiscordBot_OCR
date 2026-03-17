"""
Utility functions for determining guild assignment based on Discord channel context.
"""

import discord


def get_guild_from_channel_category(interaction: discord.Interaction) -> str | None:
    """
    Determine which game guild a user should be associated with based on 
    the Discord channel category where they're interacting.
    
    Returns:
        - "AboveAll" if the interaction is in an ABOVEALL category
        - "MoeCafe" if the interaction is in a MOECAFE category  
        - None if the category doesn't match any known guild
    """
    if not hasattr(interaction, 'channel') or not interaction.channel:
        return None
        
    channel = interaction.channel
    
    # Handle both text channels and threads
    if hasattr(channel, 'category') and channel.category:
        category_name = channel.category.name.upper()
    elif hasattr(channel, 'parent') and channel.parent and hasattr(channel.parent, 'category') and channel.parent.category:
        # For threads, get the parent channel's category
        category_name = channel.parent.category.name.upper()
    else:
        return None
    
    # Check if category name contains guild identifiers
    if "ABOVEALL" in category_name:
        return "AboveAll"
    elif "MOECAFE" in category_name:
        return "MoeCafe"
    
    # Check for category emojis/symbols that might be used
    if "👑" in category_name:
        if "ABOVEALL" in category_name:
            return "AboveAll"
        elif "MOECAFE" in category_name:
            return "MoeCafe"
    
    return None


def get_appropriate_guild_for_interaction(interaction: discord.Interaction, fallback_guild: str = None) -> str | None:
    """
    Get the most appropriate guild for a user based on their interaction context.
    
    Args:
        interaction: The Discord interaction
        fallback_guild: Guild to use if none can be determined from context
        
    Returns:
        Guild name or None
    """
    # First try to determine from channel category
    guild_from_category = get_guild_from_channel_category(interaction)
    if guild_from_category:
        return guild_from_category
    
    # If no category match, use fallback
    return fallback_guild


def format_guild_context_message(guild_name: str) -> str:
    """
    Format a message indicating which guild context is being used.
    
    Args:
        guild_name: Name of the guild
        
    Returns:
        Formatted message string
    """
    if guild_name == "AboveAll":
        return "📊 **AboveAll Guild Chat**"
    elif guild_name == "MoeCafe":
        return "📊 **MoeCafe Guild Chat**" 
    else:
        return f"📊 **{guild_name} Guild Chat**"


def validate_guild_access(interaction: discord.Interaction, required_guild: str) -> bool:
    """
    Validate if a user should have access to a specific guild based on channel context.
    
    Args:
        interaction: The Discord interaction
        required_guild: Guild name that access is being checked for
        
    Returns:
        True if access should be granted, False otherwise
    """
    detected_guild = get_guild_from_channel_category(interaction)
    
    # If we can't detect a guild from context, allow access
    if not detected_guild:
        return True
    
    # If the detected guild matches the required guild, allow access
    return detected_guild == required_guild